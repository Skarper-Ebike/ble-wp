# PACK-10S Battery Pack — BLE Interface (ICD)

Rev 2.0, maintenance edition. The pack is a BLE peripheral; your MCU is the central.

> **Situation.** After a bad firmware update this pack locked itself into
> maintenance mode and its rider is stranded. The pack opens up one stage
> ("room") at a time. Each room hides a code that your firmware has to find and
> write back to the pack. Five rooms, then the pack is released.

All multi-byte fields are **little-endian**. UUIDs are written in the usual
big-endian string form; on the wire (and in `ble_uuid_t`) they are reversed.

## Advertising

| AD structure | Content |
|---|---|
| Complete local name (0x09) | `PACK-<last 4 hex of address>`, e.g. `PACK-3A1F` |
| Manufacturer data (0xFF) | company ID `0xFFFF` (u16), current room (u8), 6 ASCII characters |

Our packs use company ID `0xFFFF`.

## Rooms

The pack's GATT table grows as rooms open, so attribute handles change.
**Always discover; never hard-code handles.**

| Room | Visible while you're in it | To open it |
|---|---|---|
| 1 | Device Information, Unlock | The code the pack advertises |
| 2 | + Battery, Telemetry | The 12-character service phrase carried in telemetry |
| 3 | + Command, Response | The diagnostic code for the pack's weak cell |
| 4 | same | Stay connected and responsive. The pack gives you a code when it's satisfied |
| 5 | same, event log unlocked | CRC-32 of the event log |

After rooms 1–3 open, the pack reboots into its next mode: expect a disconnect
(reason `0x13`) and a couple of seconds off air.

## Unlock characteristic

`8a1c0010-6b2e-4d8f-9a31-5c7e0f2b4d10`. Read, write (with response), notify.

**Write** the room you're opening (u8) followed by the code as ASCII. The
write response says how it went:

| ATT status | Meaning |
|---|---|
| `0x00` | Correct, room open |
| `0x80` | Wrong code. Three wrong in a row locks the room for 30 s |
| `0x81` | Locked out, wait |
| `0x82` | That's not the room the pack is in |
| `0x83` | No mission loaded (see README) |

**Read** returns a short status line: `ROOM 2 LOCKED`, `ROOM 4 CODE XXXXXXXX`,
`ESCAPED`. **Notifications** carry the same kind of line when something
changes.

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
| Unlock | Pack | `8a1c0010-6b2e-4d8f-9a31-5c7e0f2b4d10` | read, write, notify |
| Telemetry | Pack | `8a1c0001-6b2e-4d8f-9a31-5c7e0f2b4d10` | notify |
| Command | Pack | `8a1c0002-6b2e-4d8f-9a31-5c7e0f2b4d10` | write (with response) |
| Response | Pack | `8a1c0003-6b2e-4d8f-9a31-5c7e0f2b4d10` | notify |

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
| 16 | 2 × char | maintenance | 2 characters of the service phrase, slot `seq mod 6` |
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

Status: 0 OK, 1 unknown opcode, 2 bad length, 3 out of range, 4 busy,
5 response too large for the current MTU, 6 fault latched, 7 locked (a later room).

| Opcode | Name | Request payload | Response payload |
|---|---|---|---|
| 0x01 | GET_INFO | — | fw major u8, fw minor u8, fw patch u8, cell count u8, capacity Wh u16, log size u32, reserved u32 |
| 0x02 | GET_CELLS | — | count u8, then count × u16 mV |
| 0x03 | SET_CHARGE_LIMIT | limit u8 (%, 50–100) | — |
| 0x04 | CLEAR_FAULTS | — | — |
| 0x05 | GET_DIAG | cell u8 (1-based) | ASCII line, e.g. `CELL 03 IR 12mOhm DIAG XXXXXXXX` |
| 0x10 | LOG_READ | offset u32, length u8 | offset u32, data |

While a fault is latched the pack refuses GET_CELLS, SET_CHARGE_LIMIT and
GET_DIAG with status 6.

Responses longer than MTU − 3 are not sent; the pack replies with status 5
instead. Some responses need a larger MTU, so request one after connecting
(the pack supports up to 247).

LOG_READ returns up to `length` bytes of the pack's event log (size from
GET_INFO). The pack doesn't report the log's CRC in maintenance mode.

## Connection behaviour

- The pack accepts one connection at a time.
- It can drop the link at any time (supervision timeout, reason `0x08`).
- It reboots: after rooms open, and sometimes when it's unstable. While
  rebooting it stops advertising for a few seconds. After a reboot `seq`
  restarts at 0 and notifications are off until you subscribe again.
