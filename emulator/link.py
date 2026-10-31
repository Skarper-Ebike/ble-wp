"""Wire format between the firmware's sim driver and the emulator.

Frame: u16 length (LE, covers type + payload) | u8 type | payload
One connection only, so connection handles are implicit on the firmware side.
"""

import struct

# firmware -> emulator
SCAN_START = 0x01
SCAN_STOP = 0x02
CONNECT = 0x03      # addr[6]
DISCONNECT = 0x04
DISCOVER = 0x05
READ = 0x06         # handle u16
WRITE = 0x07        # handle u16, with_rsp u8, data
MTU_REQ = 0x08      # mtu u16

# emulator -> firmware
EVT_ADV = 0x81           # addr[6], rssi i8, len u8, data
EVT_CONNECTED = 0x82     # conn u16, addr[6]
EVT_DISCONNECTED = 0x83  # reason u8
EVT_CHAR = 0x84          # service[16], uuid[16], value_handle u16, cccd u16, props u8
EVT_DISC_DONE = 0x85     # status u8
EVT_READ_RSP = 0x86      # handle u16, status u8, data
EVT_WRITE_RSP = 0x87     # handle u16, status u8
EVT_NOTIFY = 0x88        # handle u16, data
EVT_MTU = 0x89           # mtu u16

NAMES = {v: k for k, v in globals().items() if isinstance(v, int) and k.isupper()}


def frame(ftype: int, payload: bytes = b"") -> bytes:
    return struct.pack("<HB", len(payload) + 1, ftype) + payload


async def read_frame(reader):
    hdr = await reader.readexactly(2)
    (length,) = struct.unpack("<H", hdr)
    body = await reader.readexactly(length)
    return body[0], body[1:]
