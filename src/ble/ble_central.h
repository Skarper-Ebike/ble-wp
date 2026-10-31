#ifndef BLE_CENTRAL_H
#define BLE_CENTRAL_H

/*
 * BLE central API. Modelled on a typical vendor stack (SoftDevice / NimBLE).
 * Treat this file as frozen: it's the "vendor" API, you build on top of it.
 *
 * Rules, as on a real stack:
 *  - Events are delivered to your handler on the BLE stack task. Keep the
 *    handler short and never block in it.
 *  - Pointers inside an event (read data, notification data) are only valid
 *    for the duration of the handler call. Copy what you need.
 *  - One connection at a time.
 *  - One ATT request at a time per connection: discover, read, write with
 *    response, subscribe and MTU request each occupy the link until their
 *    completion event arrives. Another request in the meantime returns
 *    BLE_ERR_BUSY. Write without response and notifications are not limited.
 *  - Writes are limited to MTU - 3 bytes.
 */

#ifdef __cplusplus
extern "C" {
#endif

#include <stdbool.h>
#include <stdint.h>

typedef uint16_t ble_conn_t;
#define BLE_CONN_INVALID 0xFFFFu

typedef struct { uint8_t addr[6]; } ble_addr_t;   /* addr[0] = LSB */
typedef struct { uint8_t b[16]; } ble_uuid_t;     /* little-endian, as on air */

typedef enum {
    BLE_OK = 0,
    BLE_ERR_NOT_READY,      /* ble_init not done / emulator not reachable */
    BLE_ERR_INVALID_STATE,  /* e.g. not connected, already connected */
    BLE_ERR_INVALID_PARAM,
    BLE_ERR_BUSY,           /* an ATT request is already outstanding */
    BLE_ERR_TOO_LARGE,      /* write longer than MTU - 3 */
    BLE_ERR_IO,
} ble_status_t;

/* Characteristic properties (Core Spec values) */
#define BLE_PROP_READ          0x02
#define BLE_PROP_WRITE_NO_RSP  0x04
#define BLE_PROP_WRITE         0x08
#define BLE_PROP_NOTIFY        0x10

/* ATT status codes seen in read/write responses */
#define BLE_ATT_OK                   0x00
#define BLE_ATT_INVALID_HANDLE       0x01
#define BLE_ATT_READ_NOT_PERMITTED   0x02
#define BLE_ATT_WRITE_NOT_PERMITTED  0x03
#define BLE_ATT_INVALID_LENGTH       0x0D

/* Disconnect reasons (HCI error codes) */
#define BLE_REASON_SUPERVISION_TIMEOUT  0x08
#define BLE_REASON_REMOTE_TERMINATED    0x13
#define BLE_REASON_LOCAL_TERMINATED     0x16
#define BLE_REASON_CONN_FAILED          0x3E

typedef enum {
    BLE_EVT_ADV_REPORT,      /* scan result */
    BLE_EVT_CONNECTED,
    BLE_EVT_DISCONNECTED,    /* also sent when a connect attempt fails */
    BLE_EVT_CHAR_DISCOVERED, /* one per characteristic during discovery */
    BLE_EVT_DISCOVERY_DONE,
    BLE_EVT_READ_RSP,
    BLE_EVT_WRITE_RSP,       /* completes write-with-response and subscribe */
    BLE_EVT_NOTIFICATION,
    BLE_EVT_MTU_UPDATED,
} ble_evt_type_t;

typedef struct {
    ble_evt_type_t type;
    ble_conn_t     conn;
    union {
        struct { ble_addr_t addr; int8_t rssi; uint8_t len; uint8_t data[31]; } adv; /* raw AD structures */
        struct { ble_addr_t addr; } connected;
        struct { uint8_t reason; } disconnected;
        struct {
            ble_uuid_t service;
            ble_uuid_t uuid;
            uint16_t   value_handle;
            uint16_t   cccd_handle;  /* 0 if the characteristic can't notify */
            uint8_t    props;        /* BLE_PROP_* */
        } chr;
        struct { uint8_t status; } discovery_done;
        struct { uint16_t handle; uint8_t status; uint16_t len; const uint8_t * data; } read_rsp;
        struct { uint16_t handle; uint8_t status; } write_rsp;
        struct { uint16_t handle; uint16_t len; const uint8_t * data; } notification;
        struct { uint16_t mtu; } mtu_updated;
    } params;
} ble_evt_t;

typedef void ( * ble_evt_handler_t )( const ble_evt_t * evt );

/* Starts the stack task and connects to the radio (the emulator).
 * Call from a task, after the scheduler has started. */
ble_status_t ble_init( ble_evt_handler_t handler );

ble_status_t ble_scan_start( void );
ble_status_t ble_scan_stop( void );

/* Result arrives as BLE_EVT_CONNECTED or BLE_EVT_DISCONNECTED. Default MTU is 23. */
ble_status_t ble_connect( const ble_addr_t * addr );
ble_status_t ble_disconnect( ble_conn_t conn );

/* Emits BLE_EVT_CHAR_DISCOVERED for each characteristic, then BLE_EVT_DISCOVERY_DONE. */
ble_status_t ble_gattc_discover( ble_conn_t conn );

ble_status_t ble_gattc_read( ble_conn_t conn, uint16_t handle );
ble_status_t ble_gattc_write( ble_conn_t conn, uint16_t handle,
                              const void * data, uint16_t len, bool with_rsp );

/* Writes the CCCD. Completes with BLE_EVT_WRITE_RSP on cccd_handle. */
ble_status_t ble_gattc_subscribe( ble_conn_t conn, uint16_t cccd_handle, bool enable );

/* Completes with BLE_EVT_MTU_UPDATED (the negotiated value may be lower). */
ble_status_t ble_mtu_request( ble_conn_t conn, uint16_t mtu );

/* Helpers */
ble_uuid_t ble_uuid16( uint16_t uuid16 );  /* expands with the Bluetooth base UUID */
bool ble_uuid_eq( const ble_uuid_t * a, const ble_uuid_t * b );

#ifdef __cplusplus
}
#endif

#endif /* BLE_CENTRAL_H */
