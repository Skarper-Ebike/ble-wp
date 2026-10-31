# PACK-10S Battery Pack — BLE Interface (ICD)

Rev 1.3. The pack is a BLE peripheral. Your MCU is the central.

All multi-byte fields are **little-endian**. UUIDs are written in the usual
big-endian string form; on the wire (and in `ble_uuid_t`) they are reversed.

## Advertising

| Field | Value |
|---|---|
| Local name | `PACK-<last 4 hex of address>`, e.g. `PACK-3A1F` |
| Service UUID (128-bit, complete list) | Pack Service, see below |
| Interval | ~200 ms |

Other devices may be advertising nearby. Only connect to a device that
advertises the Pack Service UUID.

## Services

| Service | UUID |
|---|---|
| Device Information | `0x180A` |
| Battery | `0x180F` |
| Pack | `8a1c0000-6b2e-4d8f-9a31-5c7e0f2b4d10` |

| Characteristic | Service | UUID | Properties |
|---|---|---|---|
| Manufacturer Name | DIS | `0x2A29` | read |
| Model Number | DIS | `0x2A24` | read |
| Serial Number | DIS | `0x2A25` | read |
| Firmware Revision | DIS | `0x2A26` | read |
| Battery Level | Battery | `0x2A19` | read, notify (uint8, %) |
| Telemetry | Pack | `8a1c0001-6b2e-4d8f-9a31-5c7e0f2b4d10` | notify |
| Command | Pack | `8a1c0002-6b2e-4d8f-9a31-5c7e0f2b4d10` | write (with response) |
| Response | Pack | `8a1c0003-6b2e-4d8f-9a31-5c7e0f2b4d10` | notify |

Attribute handles are assigned by the pack and may change between pack
firmware versions. Discover them; don't hard-code them.

## Telemetry (notify, 10 Hz)

20 bytes, fits the default MTU.

| Offset | Type | Field | Unit / notes |
|---|---|---|---|
| 0 | u16 | seq | +1 per frame, wraps. Resets to 0 when the pack boots |
| 2 | u16 | pack_voltage | mV |
| 4 | i16 | current | mA, positive = discharging |
| 6 | i16 | power | W, positive = discharging |
| 8 | u8 | soc | % |
| 9 | i8 | temperature | °C |
| 10 | u16 | cell_min | mV |
| 12 | u16 | cell_max | mV |
| 14 | u16 | flags | see below |
| 16 | u16 | reserved | 0 |
| 18 | u16 | crc | CRC-16/CCITT-FALSE over bytes 0–17 |

Flags: bit0 charging, bit1 discharging, bit2 over-temperature warning,
bit3 cell imbalance (>50 mV), bit4 fault latched (cleared by `CLEAR_FAULTS`).

Frames with a bad CRC must be discarded. Notifications are not acknowledged,
so frames can be lost.

## Commands

Write a request to **Command**. The reply arrives as a notification on
**Response**, so subscribe to Response first. A write response only means the
pack received the request, not that it executed it.

Request:

| Offset | Type | Field |
|---|---|---|
| 0 | u8 | req_id (chosen by you, echoed back) |
| 1 | u8 | opcode |
| 2 | u8 | len (payload bytes) |
| 3 | … | payload |

Response:

| Offset | Type | Field |
|---|---|---|
| 0 | u8 | req_id |
| 1 | u8 | opcode |
| 2 | u8 | status |
| 3 | u8 | len |
| 4 | … | payload |

The pack handles one command at a time and usually answers within 500 ms.
It may be slower, and it may not answer at all. Allow 2 s before giving up.

Status: 0 OK, 1 unknown opcode, 2 bad length, 3 out of range,
4 busy, 5 response too large for the current MTU.

| Opcode | Name | Request payload | Response payload |
|---|---|---|---|
| 0x01 | GET_INFO | — | fw major u8, fw minor u8, fw patch u8, cell count u8, capacity Wh u16, log size u32, log CRC-32 u32 |
| 0x02 | GET_CELLS | — | count u8, then count × u16 mV |
| 0x03 | SET_CHARGE_LIMIT | limit u8 (%, 50–100) | — |
| 0x04 | CLEAR_FAULTS | — | — |
| 0x10 | LOG_READ | offset u32, length u8 | offset u32, data |

Responses longer than MTU − 3 are not sent; the pack replies with status 5
instead. GET_CELLS needs a larger MTU, so request one after connecting
(the pack supports up to 247).

LOG_READ returns up to `length` bytes from the pack's event log. Read the
whole log and check it against the CRC-32 (IEEE 802.3, as in zlib) from
GET_INFO.

## Connection behaviour

- The pack accepts one connection at a time.
- It can drop the link at any time (supervision timeout, reason `0x08`).
- It reboots occasionally. While rebooting it stops advertising for a few
  seconds. After a reboot `seq` restarts at 0 and notifications are off until
  you subscribe again.
