/*
 * Starter application: brings the radio up and prints what it can see.
 * Replace this with your implementation (see README).
 */

#include "application/app.h"

#include <stdio.h>
#include <string.h>

#include "FreeRTOS.h"
#include "task.h"

#include "ble/ble_central.h"

static void print_adv( const ble_evt_t * evt )
{
    /* Print each address once. */
    static ble_addr_t seen[ 8 ];
    static size_t n_seen;
    for( size_t i = 0; i < n_seen; i++ )
    {
        if( memcmp( &seen[ i ], &evt->params.adv.addr, sizeof( ble_addr_t ) ) == 0 ) return;
    }
    if( n_seen < 8 ) seen[ n_seen++ ] = evt->params.adv.addr;

    const uint8_t * a = evt->params.adv.addr.addr;
    printf( "[app] found %02X:%02X:%02X:%02X:%02X:%02X rssi %d, %u bytes of adv data\n",
            a[ 5 ], a[ 4 ], a[ 3 ], a[ 2 ], a[ 1 ], a[ 0 ], evt->params.adv.rssi, evt->params.adv.len );
}

static void on_ble_event( const ble_evt_t * evt )
{
    /* Runs on the BLE stack task. Keep it short, don't block. */
    switch( evt->type )
    {
        case BLE_EVT_ADV_REPORT:
            print_adv( evt );
            break;
        default:
            break;
    }
}

void app_task( void * arg )
{
    ( void ) arg;

    if( ble_init( on_ble_event ) != BLE_OK )
    {
        printf( "[app] ble_init failed\n" );
        vTaskDelete( NULL );
    }
    ble_scan_start();

    for( ;; )
    {
        vTaskDelay( pdMS_TO_TICKS( 1000 ) );
    }
}
