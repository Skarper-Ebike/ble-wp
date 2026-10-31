/*
 * Simulated implementation of ble_central.h. The "radio" is a TCP link to the
 * emulator (emulator/emulator.py). You shouldn't need to change this file.
 *
 * Environment: BLE_EMU_HOST (default 127.0.0.1), BLE_EMU_PORT (default 7700),
 *              BLE_TRACE=1 prints every frame.
 *
 * Wire format: u16 length (LE, type + payload) | u8 type | payload.
 * See emulator/link.py for the frame types.
 */

#include "ble/ble_central.h"

#include <errno.h>
#include <fcntl.h>
#include <netdb.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

#include "FreeRTOS.h"
#include "semphr.h"
#include "task.h"

enum {
    TX_SCAN_START = 0x01, TX_SCAN_STOP, TX_CONNECT, TX_DISCONNECT,
    TX_DISCOVER, TX_READ, TX_WRITE, TX_MTU_REQ,
    RX_ADV = 0x81, RX_CONNECTED, RX_DISCONNECTED, RX_CHAR, RX_DISC_DONE,
    RX_READ_RSP, RX_WRITE_RSP, RX_NOTIFY, RX_MTU,
};

#define STACK_TASK_PRIO   ( tskIDLE_PRIORITY + 3 )
#define STACK_TASK_DEPTH  ( configMINIMAL_STACK_SIZE * 4 )
#define RX_BUF_SIZE       4096
#define CONNECT_WAIT_MS   30000

static int s_sock = -1;
static ble_evt_handler_t s_handler;
static SemaphoreHandle_t s_lock;
static bool s_trace;

/* link state, guarded by s_lock */
static ble_conn_t s_conn = BLE_CONN_INVALID;
static bool s_connecting;
static bool s_att_busy;
static uint16_t s_mtu = 23;

static uint8_t s_rx[ RX_BUF_SIZE ];
static size_t s_rx_len;

static uint16_t rd16( const uint8_t * p ) { return ( uint16_t ) ( p[ 0 ] | ( p[ 1 ] << 8 ) ); }

static ble_status_t send_frame( uint8_t type, const uint8_t * a, size_t alen,
                                const uint8_t * b, size_t blen )
{
    uint8_t buf[ 3 + 300 ];
    size_t len = 1 + alen + blen;
    if( len + 2 > sizeof( buf ) ) return BLE_ERR_INVALID_PARAM;
    buf[ 0 ] = ( uint8_t ) len;
    buf[ 1 ] = ( uint8_t ) ( len >> 8 );
    buf[ 2 ] = type;
    if( alen ) memcpy( &buf[ 3 ], a, alen );
    if( blen ) memcpy( &buf[ 3 + alen ], b, blen );

    if( s_trace ) printf( "[ble] tx type=0x%02x len=%zu\n", type, len - 1 );

    size_t off = 0;
    while( off < len + 2 )
    {
        ssize_t n = send( s_sock, buf + off, len + 2 - off, MSG_NOSIGNAL );
        if( n > 0 ) { off += ( size_t ) n; continue; }
        if( n < 0 && ( errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR ) )
        {
            vTaskDelay( 1 );
            continue;
        }
        return BLE_ERR_IO;
    }
    return BLE_OK;
}

/* ---------------------------------------------------------------- rx path */

static void dispatch( uint8_t type, const uint8_t * p, size_t n )
{
    ble_evt_t e;
    memset( &e, 0, sizeof( e ) );

    xSemaphoreTake( s_lock, portMAX_DELAY );
    e.conn = s_conn;
    switch( type )
    {
        case RX_ADV:
            if( n < 8 ) goto bad;
            e.type = BLE_EVT_ADV_REPORT;
            memcpy( e.params.adv.addr.addr, p, 6 );
            e.params.adv.rssi = ( int8_t ) p[ 6 ];
            e.params.adv.len = p[ 7 ] > 31 ? 31 : p[ 7 ];
            if( n < 8u + e.params.adv.len ) goto bad;
            memcpy( e.params.adv.data, p + 8, e.params.adv.len );
            e.conn = BLE_CONN_INVALID;
            break;
        case RX_CONNECTED:
            if( n < 8 ) goto bad;
            s_conn = rd16( p );
            s_connecting = false;
            s_att_busy = false;
            s_mtu = 23;
            e.type = BLE_EVT_CONNECTED;
            e.conn = s_conn;
            memcpy( e.params.connected.addr.addr, p + 2, 6 );
            break;
        case RX_DISCONNECTED:
            if( n < 1 ) goto bad;
            e.type = BLE_EVT_DISCONNECTED;
            e.params.disconnected.reason = p[ 0 ];
            s_conn = BLE_CONN_INVALID;
            s_connecting = false;
            s_att_busy = false;
            s_mtu = 23;
            break;
        case RX_CHAR:
            if( n < 37 ) goto bad;
            e.type = BLE_EVT_CHAR_DISCOVERED;
            memcpy( e.params.chr.service.b, p, 16 );
            memcpy( e.params.chr.uuid.b, p + 16, 16 );
            e.params.chr.value_handle = rd16( p + 32 );
            e.params.chr.cccd_handle = rd16( p + 34 );
            e.params.chr.props = p[ 36 ];
            break;
        case RX_DISC_DONE:
            e.type = BLE_EVT_DISCOVERY_DONE;
            e.params.discovery_done.status = n ? p[ 0 ] : 0;
            s_att_busy = false;
            break;
        case RX_READ_RSP:
            if( n < 3 ) goto bad;
            e.type = BLE_EVT_READ_RSP;
            e.params.read_rsp.handle = rd16( p );
            e.params.read_rsp.status = p[ 2 ];
            e.params.read_rsp.len = ( uint16_t ) ( n - 3 );
            e.params.read_rsp.data = p + 3;
            s_att_busy = false;
            break;
        case RX_WRITE_RSP:
            if( n < 3 ) goto bad;
            e.type = BLE_EVT_WRITE_RSP;
            e.params.write_rsp.handle = rd16( p );
            e.params.write_rsp.status = p[ 2 ];
            s_att_busy = false;
            break;
        case RX_NOTIFY:
            if( n < 2 ) goto bad;
            e.type = BLE_EVT_NOTIFICATION;
            e.params.notification.handle = rd16( p );
            e.params.notification.len = ( uint16_t ) ( n - 2 );
            e.params.notification.data = p + 2;
            break;
        case RX_MTU:
            if( n < 2 ) goto bad;
            e.type = BLE_EVT_MTU_UPDATED;
            s_mtu = rd16( p );
            e.params.mtu_updated.mtu = s_mtu;
            s_att_busy = false;
            break;
        default:
            goto bad;
    }
    xSemaphoreGive( s_lock );

    if( s_trace ) printf( "[ble] rx type=0x%02x len=%zu\n", type, n );
    if( s_handler ) s_handler( &e );
    return;

bad:
    xSemaphoreGive( s_lock );
    printf( "[ble] dropped malformed frame type=0x%02x len=%zu\n", type, n );
}

static void stack_task( void * arg )
{
    ( void ) arg;
    for( ;; )
    {
        ssize_t n = recv( s_sock, s_rx + s_rx_len, sizeof( s_rx ) - s_rx_len, MSG_DONTWAIT );
        if( n == 0 )
        {
            printf( "[ble] emulator closed the radio link, exiting\n" );
            fflush( stdout );
            _exit( 2 );
        }
        if( n < 0 )
        {
            if( errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR )
            {
                printf( "[ble] radio link error: %s, exiting\n", strerror( errno ) );
                fflush( stdout );
                _exit( 2 );
            }
            vTaskDelay( 1 );
            continue;
        }
        s_rx_len += ( size_t ) n;

        size_t off = 0;
        while( s_rx_len - off >= 3 )
        {
            size_t len = rd16( s_rx + off );
            if( len == 0 || len > RX_BUF_SIZE - 2 ) { s_rx_len = off = 0; break; }
            if( s_rx_len - off < 2 + len ) break;
            dispatch( s_rx[ off + 2 ], s_rx + off + 3, len - 1 );
            off += 2 + len;
        }
        if( off )
        {
            memmove( s_rx, s_rx + off, s_rx_len - off );
            s_rx_len -= off;
        }
    }
}

/* ---------------------------------------------------------------- init */

static int open_link( const char * host, const char * port )
{
    struct addrinfo hints = { 0 }, * res = NULL;
    hints.ai_family = AF_INET;
    hints.ai_socktype = SOCK_STREAM;
    if( getaddrinfo( host, port, &hints, &res ) != 0 || !res ) return -1;

    int fd = socket( res->ai_family, res->ai_socktype, res->ai_protocol );
    if( fd < 0 ) { freeaddrinfo( res ); return -1; }
    fcntl( fd, F_SETFL, fcntl( fd, F_GETFL ) | O_NONBLOCK );

    int rc = connect( fd, res->ai_addr, res->ai_addrlen );
    freeaddrinfo( res );
    if( rc == 0 ) return fd;
    if( errno != EINPROGRESS && errno != EINTR ) { close( fd ); return -1; }

    for( int i = 0; i < 200; i++ )   /* ~2 s */
    {
        struct pollfd pfd = { .fd = fd, .events = POLLOUT };
        if( poll( &pfd, 1, 0 ) > 0 )
        {
            int err = 0;
            socklen_t l = sizeof( err );
            getsockopt( fd, SOL_SOCKET, SO_ERROR, &err, &l );
            if( err == 0 ) return fd;
            break;
        }
        vTaskDelay( pdMS_TO_TICKS( 10 ) );
    }
    close( fd );
    return -1;
}

ble_status_t ble_init( ble_evt_handler_t handler )
{
    if( s_sock >= 0 ) return BLE_ERR_INVALID_STATE;

    const char * host = getenv( "BLE_EMU_HOST" );
    const char * port = getenv( "BLE_EMU_PORT" );
    host = host ? host : "127.0.0.1";
    port = port ? port : "7700";
    s_trace = getenv( "BLE_TRACE" ) != NULL;

    printf( "[ble] connecting to emulator at %s:%s\n", host, port );
    TickType_t start = xTaskGetTickCount();
    while( ( s_sock = open_link( host, port ) ) < 0 )
    {
        if( xTaskGetTickCount() - start > pdMS_TO_TICKS( CONNECT_WAIT_MS ) )
        {
            printf( "[ble] emulator not reachable\n" );
            return BLE_ERR_NOT_READY;
        }
        vTaskDelay( pdMS_TO_TICKS( 500 ) );
    }

    s_handler = handler;
    s_lock = xSemaphoreCreateMutex();
    configASSERT( s_lock );
    BaseType_t rc = xTaskCreate( stack_task, "ble_stack", STACK_TASK_DEPTH, NULL, STACK_TASK_PRIO, NULL );
    configASSERT( rc == pdPASS );
    printf( "[ble] radio up\n" );
    return BLE_OK;
}

/* ---------------------------------------------------------------- tx path */

static ble_status_t simple( uint8_t type, const uint8_t * p, size_t n )
{
    if( s_sock < 0 ) return BLE_ERR_NOT_READY;
    xSemaphoreTake( s_lock, portMAX_DELAY );
    ble_status_t st = send_frame( type, p, n, NULL, 0 );
    xSemaphoreGive( s_lock );
    return st;
}

/* Claims the single ATT slot and sends. */
static ble_status_t att_request( ble_conn_t conn, uint8_t type,
                                 const uint8_t * a, size_t alen,
                                 const uint8_t * b, size_t blen, bool occupies )
{
    if( s_sock < 0 ) return BLE_ERR_NOT_READY;
    xSemaphoreTake( s_lock, portMAX_DELAY );
    ble_status_t st = BLE_OK;
    if( s_conn == BLE_CONN_INVALID || conn != s_conn ) st = BLE_ERR_INVALID_STATE;
    else if( occupies && s_att_busy ) st = BLE_ERR_BUSY;
    else
    {
        st = send_frame( type, a, alen, b, blen );
        if( st == BLE_OK && occupies ) s_att_busy = true;
    }
    xSemaphoreGive( s_lock );
    return st;
}

ble_status_t ble_scan_start( void ) { return simple( TX_SCAN_START, NULL, 0 ); }
ble_status_t ble_scan_stop( void )  { return simple( TX_SCAN_STOP, NULL, 0 ); }

ble_status_t ble_connect( const ble_addr_t * addr )
{
    if( !addr ) return BLE_ERR_INVALID_PARAM;
    if( s_sock < 0 ) return BLE_ERR_NOT_READY;
    xSemaphoreTake( s_lock, portMAX_DELAY );
    ble_status_t st = BLE_OK;
    if( s_conn != BLE_CONN_INVALID || s_connecting ) st = BLE_ERR_INVALID_STATE;
    else
    {
        st = send_frame( TX_CONNECT, addr->addr, 6, NULL, 0 );
        if( st == BLE_OK ) s_connecting = true;
    }
    xSemaphoreGive( s_lock );
    return st;
}

ble_status_t ble_disconnect( ble_conn_t conn )
{
    return att_request( conn, TX_DISCONNECT, NULL, 0, NULL, 0, false );
}

ble_status_t ble_gattc_discover( ble_conn_t conn )
{
    return att_request( conn, TX_DISCOVER, NULL, 0, NULL, 0, true );
}

ble_status_t ble_gattc_read( ble_conn_t conn, uint16_t handle )
{
    uint8_t h[ 2 ] = { ( uint8_t ) handle, ( uint8_t ) ( handle >> 8 ) };
    return att_request( conn, TX_READ, h, 2, NULL, 0, true );
}

ble_status_t ble_gattc_write( ble_conn_t conn, uint16_t handle,
                              const void * data, uint16_t len, bool with_rsp )
{
    if( len && !data ) return BLE_ERR_INVALID_PARAM;
    xSemaphoreTake( s_lock, portMAX_DELAY );
    uint16_t mtu = s_mtu;
    xSemaphoreGive( s_lock );
    if( len > mtu - 3 ) return BLE_ERR_TOO_LARGE;
    uint8_t hdr[ 3 ] = { ( uint8_t ) handle, ( uint8_t ) ( handle >> 8 ), with_rsp ? 1 : 0 };
    return att_request( conn, TX_WRITE, hdr, 3, data, len, with_rsp );
}

ble_status_t ble_gattc_subscribe( ble_conn_t conn, uint16_t cccd_handle, bool enable )
{
    uint8_t v[ 2 ] = { enable ? 1 : 0, 0 };
    return ble_gattc_write( conn, cccd_handle, v, 2, true );
}

ble_status_t ble_mtu_request( ble_conn_t conn, uint16_t mtu )
{
    if( mtu < 23 ) return BLE_ERR_INVALID_PARAM;
    uint8_t m[ 2 ] = { ( uint8_t ) mtu, ( uint8_t ) ( mtu >> 8 ) };
    return att_request( conn, TX_MTU_REQ, m, 2, NULL, 0, true );
}
