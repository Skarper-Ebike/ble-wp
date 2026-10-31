#include <string.h>

#include "ble/ble_central.h"

ble_uuid_t ble_uuid16( uint16_t u )
{
    /* 0000xxxx-0000-1000-8000-00805f9b34fb, little-endian */
    ble_uuid_t r = { { 0xfb, 0x34, 0x9b, 0x5f, 0x80, 0x00, 0x00, 0x80,
                       0x00, 0x10, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00 } };
    r.b[ 12 ] = ( uint8_t ) u;
    r.b[ 13 ] = ( uint8_t ) ( u >> 8 );
    return r;
}

bool ble_uuid_eq( const ble_uuid_t * a, const ble_uuid_t * b )
{
    return memcmp( a->b, b->b, 16 ) == 0;
}
