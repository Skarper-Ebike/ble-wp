#ifndef APPLICATION_PROTOCOL_H
#define APPLICATION_PROTOCOL_H

/* Pure protocol code (no FreeRTOS, no BLE calls) lives here so it can be
 * unit tested on the host. Add your decoders/encoders next to this. */

#ifdef __cplusplus
extern "C" {
#endif

#include <stddef.h>
#include <stdint.h>

/* CRC-16/CCITT-FALSE: poly 0x1021, init 0xFFFF, no reflection, no xorout. */
uint16_t crc16_ccitt( const uint8_t * data, size_t len );

#ifdef __cplusplus
}
#endif

#endif /* APPLICATION_PROTOCOL_H */
