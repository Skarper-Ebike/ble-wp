#!/usr/bin/env python3
"""Fake BLE world for the firmware: a battery pack (PACK-3A1F) plus two decoys.

The firmware's sim driver connects over TCP (default :7700). Faults can be
injected live through the control port (default :7701), see ctl.py.

Usage: python3 emulator.py [--scenario NAME] [--port 7700] [--ctl-port 7701]
"""

import argparse
import asyncio
import json
import math
import os
import random
import struct
import sys
import time
import zlib

import link

HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------- helpers

BASE_UUID = "0000{:04x}-0000-1000-8000-00805f9b34fb"


def uuid128(s: str) -> bytes:
    return bytes.fromhex(s.replace("-", ""))[::-1]


def uuid16(u: int) -> bytes:
    return uuid128(BASE_UUID.format(u))


def addr_bytes(s: str) -> bytes:
    return bytes(int(x, 16) for x in s.split(":"))[::-1]


def crc16_ccitt(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else crc << 1
            crc &= 0xFFFF
    return crc


def log(msg: str) -> None:
    print(f"[emu {time.strftime('%H:%M:%S')}] {msg}", flush=True)


PROP_READ, PROP_WRITE, PROP_NOTIFY = 0x02, 0x08, 0x10
ATT_OK, ATT_INVALID_HANDLE, ATT_READ_NOT_PERMITTED, ATT_WRITE_NOT_PERMITTED, ATT_INVALID_LEN = 0, 1, 2, 3, 0x0D

PACK_SVC = "8a1c0000-6b2e-4d8f-9a31-5c7e0f2b4d10"
PACK_TELEM = "8a1c0001-6b2e-4d8f-9a31-5c7e0f2b4d10"
PACK_CMD = "8a1c0002-6b2e-4d8f-9a31-5c7e0f2b4d10"
PACK_RSP = "8a1c0003-6b2e-4d8f-9a31-5c7e0f2b4d10"

ST_OK, ST_UNKNOWN, ST_BAD_LEN, ST_RANGE, ST_BUSY, ST_TOO_LARGE = range(6)

DEFAULT_SCENARIO = {
    "telemetry_hz": 10,
    "drop_rate": 0.02,           # fraction of telemetry notifications lost
    "crc_error_rate": 0.0,       # fraction of telemetry frames with a bad CRC
    "resp_delay_ms": [50, 400],  # command response delay range
    "resp_loss_rate": 0.0,       # fraction of commands never answered
    "connect_fail_rate": 0.0,
    "link_drop_every_s": None,   # [min, max] seconds between supervision timeouts
    "reboot_every_s": None,      # [min, max] seconds between pack reboots
    "reboot_downtime_s": 5,
    "handle_base": 1,            # first attribute handle of the pack's GATT table
    "seed": None,
}


# ---------------------------------------------------------------- GATT

class Char:
    def __init__(self, service, uuid, props, read=None, write=None):
        self.service, self.uuid, self.props = service, uuid, props
        self.read, self.write = read, write
        self.value_handle = self.cccd_handle = 0
        self.notifying = False


def assign_handles(chars, base):
    h = base
    last_svc = None
    for c in chars:
        if c.service != last_svc:
            h += 1  # service declaration
            last_svc = c.service
        h += 1      # characteristic declaration
        c.value_handle = h
        h += 1
        if c.props & PROP_NOTIFY:
            c.cccd_handle = h
            h += 1


# ---------------------------------------------------------------- devices

class Device:
    def __init__(self, world, addr, name, adv_uuids, rssi):
        self.world, self.addr, self.name, self.rssi = world, addr, name, rssi
        self.adv_uuids = adv_uuids
        self.advertising = True
        self.rebooting = False
        self.chars = []

    def adv_data(self) -> bytes:
        name = self.name.encode()
        ad = bytes([len(name) + 1, 0x09]) + name
        u16 = [u for u in self.adv_uuids if len(u) == 2]
        u128 = [u for u in self.adv_uuids if len(u) == 16]
        if u16:
            ad += bytes([2 * len(u16) + 1, 0x03]) + b"".join(u16)
        for u in u128:
            ad += bytes([17, 0x07]) + u
        return ad[:31]

    def on_connect(self):
        pass

    def on_disconnect(self):
        for c in self.chars:
            c.notifying = False

    def by_handle(self, h):
        for c in self.chars:
            if h in (c.value_handle, c.cccd_handle):
                return c
        return None


def dis_chars(manufacturer, model, serial, fw):
    svc = uuid16(0x180A)
    return [
        Char(svc, uuid16(0x2A29), PROP_READ, read=lambda: manufacturer.encode()),
        Char(svc, uuid16(0x2A24), PROP_READ, read=lambda: model.encode()),
        Char(svc, uuid16(0x2A25), PROP_READ, read=lambda: serial.encode()),
        Char(svc, uuid16(0x2A26), PROP_READ, read=lambda: fw.encode()),
    ]


class Decoy(Device):
    def __init__(self, world, addr, name, adv_uuid, svc_uuid, chr_uuid, rssi, model):
        super().__init__(world, addr, name, [adv_uuid], rssi)
        self.chars = dis_chars("Northwind", model, "NW" + name[-4:], "1.0.3")
        self.chars.append(Char(svc_uuid, chr_uuid, PROP_READ | PROP_NOTIFY, read=lambda: b"\x00"))
        assign_handles(self.chars, 1)


class Pack(Device):
    FW = (2, 4, 1)
    CELLS = 10
    CAPACITY_WH = 500
    CAPACITY_AH = 13.9
    CELL_OFFSETS_MV = [0, 3, -2, 5, -4, 1, -1, 2, -38, 4]

    def __init__(self, world):
        super().__init__(world, "C0:FF:EE:00:3A:1F", "PACK-3A1F", [uuid128(PACK_SVC)], -58)
        self.soc = 0.87
        self.charging = False
        self.temp = 24.0
        self.charge_limit = 100
        self.fault_latched = True   # left over from the previous ride
        self.cmd_busy = False
        self.log_blob = self._make_log()
        self._boot()

        svc = uuid128(PACK_SVC)
        self.batt_level = Char(uuid16(0x180F), uuid16(0x2A19), PROP_READ | PROP_NOTIFY,
                               read=lambda: bytes([round(self.soc * 100)]))
        self.telem = Char(svc, uuid128(PACK_TELEM), PROP_NOTIFY)
        self.cmd = Char(svc, uuid128(PACK_CMD), PROP_WRITE, write=self._on_command)
        self.rsp = Char(svc, uuid128(PACK_RSP), PROP_NOTIFY)
        self.chars = dis_chars("Volta Packs Ltd", "PK-10S-500", "VP2311003A1F",
                               ".".join(map(str, self.FW)))
        self.chars += [self.batt_level, self.telem, self.cmd, self.rsp]
        assign_handles(self.chars, world.scn["handle_base"])

    def _boot(self):
        self.seq = 0
        self.boot_t = time.monotonic()
        self.last_t = self.boot_t
        self.current = 0.0

    def _make_log(self) -> bytes:
        rng = random.Random(1234)
        lines = ["timestamp,event,detail"]
        t = 1760000000
        events = ["BOOT", "RIDE_START", "RIDE_END", "CHARGE_START", "CHARGE_END",
                  "TEMP_WARN", "CELL_IMBALANCE", "FAULT_OC", "FAULT_CLEARED"]
        while len("\n".join(lines)) < 4000:
            t += rng.randint(60, 7200)
            ev = rng.choice(events)
            lines.append(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(t))},{ev},"
                         f"soc={rng.randint(5, 100)};t={rng.randint(12, 48)}C")
        return ("\n".join(lines) + "\n").encode()

    # -- physics ---------------------------------------------------------

    def step(self):
        now = time.monotonic()
        dt, self.last_t = now - self.last_t, now
        t = now - self.boot_t
        if self.soc < 0.10:
            self.charging = True
        elif self.soc > 0.90:
            self.charging = False
        if self.charging:
            i = -4.5 + random.gauss(0, 0.1)
        else:
            i = 6 + 5 * math.sin(t / 7) + 3 * math.sin(t / 2.3) + random.gauss(0, 0.4)
            if math.sin(t / 23) > 0.85:   # stopped at a junction
                i = random.gauss(0.15, 0.03)
            i = max(0.0, i)
        self.current = i
        # time runs 3x faster for the battery so SoC visibly moves
        self.soc = min(1.0, max(0.0, self.soc - self.current * dt / 3600 / self.CAPACITY_AH * 3))
        self.temp += (24 + 1.2 * abs(self.current) - self.temp) * min(1.0, dt / 60)

    def cells_mv(self):
        ocv = 3000 + 1200 * self.soc
        cells = []
        for k, off in enumerate(self.CELL_OFFSETS_MV):
            sag = self.current * 10           # 10 mOhm per cell
            if k == 8:
                sag += abs(self.current) * 6  # weak cell is further off under any load
            cells.append(int(ocv + off - sag + random.gauss(0, 1)))
        return cells

    def telemetry_frame(self, corrupt=False) -> bytes:
        cells = self.cells_mv()
        v_mv = sum(cells)
        power = v_mv / 1000 * self.current
        flags = 0
        if self.current < -0.2:
            flags |= 1 << 0
        if self.current > 0.2:
            flags |= 1 << 1
        if self.temp > 45:
            flags |= 1 << 2
        if max(cells) - min(cells) > 50:
            flags |= 1 << 3
        if self.fault_latched:
            flags |= 1 << 4
        body = struct.pack("<HHhhBbHHHH", self.seq & 0xFFFF, v_mv, int(round(self.current * 100)),
                           int(round(power)), round(self.soc * 100), int(round(self.temp)),
                           min(cells), max(cells), flags, 0)
        crc = crc16_ccitt(body)
        if corrupt:
            crc ^= 0x5A5A
        self.seq = (self.seq + 1) & 0xFFFF
        return body + struct.pack("<H", crc)

    # -- commands --------------------------------------------------------

    def on_connect(self):
        self.cmd_busy = False

    def _on_command(self, data: bytes):
        if len(data) < 3:
            return
        req_id, op, ln = data[0], data[1], data[2]
        payload = data[3:]
        if op != 0x10:   # log reads come in bursts, keep the console readable
            log(f"command req_id={req_id} op=0x{op:02x} payload={payload.hex() or '-'}")
        if self.cmd_busy:
            self.world.spawn(self._respond(req_id, op, ST_BUSY, b"", 0.01))
            return
        self.cmd_busy = True
        scn = self.world.scn
        lo, hi = scn["resp_delay_ms"]
        delay = random.uniform(lo, hi) / 1000
        if op == 0x10:
            delay /= 10   # log reads are quick
        if self.world.silent_until > time.monotonic() or random.random() < scn["resp_loss_rate"]:
            log(f"  (not answering req_id={req_id})")
            self.world.spawn(self._clear_busy(delay))
            return
        if ln != len(payload):
            status, out = ST_BAD_LEN, b""
        else:
            status, out = self._execute(op, payload)
        self.world.spawn(self._respond(req_id, op, status, out, delay))

    def _execute(self, op, p):
        if op == 0x01:
            if p:
                return ST_BAD_LEN, b""
            return ST_OK, struct.pack("<BBBBHII", *self.FW, self.CELLS, self.CAPACITY_WH,
                                      len(self.log_blob), zlib.crc32(self.log_blob))
        if op == 0x02:
            if p:
                return ST_BAD_LEN, b""
            cells = self.cells_mv()
            return ST_OK, bytes([len(cells)]) + struct.pack(f"<{len(cells)}H", *cells)
        if op == 0x03:
            if len(p) != 1:
                return ST_BAD_LEN, b""
            if not 50 <= p[0] <= 100:
                return ST_RANGE, b""
            self.charge_limit = p[0]
            return ST_OK, b""
        if op == 0x04:
            if p:
                return ST_BAD_LEN, b""
            self.fault_latched = False
            return ST_OK, b""
        if op == 0x10:
            if len(p) != 5:
                return ST_BAD_LEN, b""
            offset, length = struct.unpack("<IB", p)
            if length == 0:
                return ST_BAD_LEN, b""
            if offset >= len(self.log_blob):
                return ST_RANGE, b""
            return ST_OK, struct.pack("<I", offset) + self.log_blob[offset:offset + length]
        return ST_UNKNOWN, b""

    async def _clear_busy(self, delay):
        await asyncio.sleep(delay)
        self.cmd_busy = False

    async def _respond(self, req_id, op, status, payload, delay):
        await asyncio.sleep(delay)
        if status != ST_BUSY:
            self.cmd_busy = False
        if self.world.connected is not self:
            return
        if 4 + len(payload) > self.world.mtu - 3:
            status, payload = ST_TOO_LARGE, b""
        if op != 0x10 or status != ST_OK:
            log(f"  response req_id={req_id} op=0x{op:02x} status={status} len={len(payload)}")
        self.world.notify(self.rsp, bytes([req_id, op, status, len(payload) & 0xFF]) + payload)


# ---------------------------------------------------------------- world

class World:
    def __init__(self, scn):
        self.scn = scn
        self.writer = None
        self.scanning = False
        self.connected = None
        self.conn_handle = 0
        self.mtu = 23
        self.tasks = set()
        self.storm_until = self.corrupt_until = self.silent_until = 0.0
        self.stats = {"telemetry_sent": 0, "telemetry_dropped": 0, "crc_corrupted": 0}
        self.transcript = None
        log_dir = os.environ.get("LOG_DIR", os.path.join(HERE, "..", "logs"))
        os.makedirs(log_dir, exist_ok=True)
        self.transcript = open(os.path.join(log_dir, "transcript.jsonl"), "a", buffering=1)
        self.pack = Pack(self)
        self.devices = [
            self.pack,
            Decoy(self, "C0:FF:EE:00:77:C0", "PACK-77C0",
                  uuid128("8a1c0000-6b2e-4d8f-9a31-5c7e0f2b4d11"),
                  uuid128("8a1c0000-6b2e-4d8f-9a31-5c7e0f2b4d11"),
                  uuid128("8a1c0001-6b2e-4d8f-9a31-5c7e0f2b4d11"), -71, "PK-10S-400"),
            Decoy(self, "D4:12:9B:00:00:42", "HRM-0042", struct.pack("<H", 0x180D), uuid16(0x180D),
                  uuid16(0x2A37), -80, "HRM-2"),
        ]

    # -- plumbing --------------------------------------------------------

    def spawn(self, coro):
        t = asyncio.ensure_future(coro)
        self.tasks.add(t)
        t.add_done_callback(self.tasks.discard)

    def record(self, direction, ftype, payload):
        self.transcript.write(json.dumps({"t": round(time.time(), 3), "dir": direction,
                                          "type": link.NAMES.get(ftype, hex(ftype)),
                                          "data": payload.hex()}) + "\n")

    def send(self, ftype, payload=b""):
        if not self.writer:
            return
        self.record("tx", ftype, payload)
        self.writer.write(link.frame(ftype, payload))

    def notify(self, char, value):
        if self.connected and char.notifying:
            self.send(link.EVT_NOTIFY, struct.pack("<H", char.value_handle) + value[: self.mtu - 3])

    async def att_delay(self):
        await asyncio.sleep(random.uniform(0.015, 0.030))   # ~ connection interval

    # -- link events -----------------------------------------------------

    def drop_link(self, reason=0x08, why="supervision timeout"):
        dev = self.connected
        if not dev:
            return
        log(f"link to {dev.name} lost ({why})")
        dev.on_disconnect()
        self.connected = None
        self.mtu = 23
        self.send(link.EVT_DISCONNECTED, bytes([reason]))
        self.spawn(self._readvertise(dev, 0.5))

    async def _readvertise(self, dev, after):
        await asyncio.sleep(after)
        if self.connected is not dev and not dev.rebooting:
            dev.advertising = True

    async def reboot_pack(self, downtime=None):
        downtime = self.scn["reboot_downtime_s"] if downtime is None else downtime
        log(f"pack rebooting ({downtime}s)")
        self.pack.rebooting = True
        self.pack.advertising = False
        if self.connected is self.pack:
            self.drop_link(why="pack reboot")
        self.pack._boot()
        await asyncio.sleep(downtime)
        self.pack.rebooting = False
        self.pack.advertising = True
        log("pack advertising again")

    # -- firmware requests -------------------------------------------------

    async def handle(self, ftype, p):
        self.record("rx", ftype, p)
        if ftype == link.SCAN_START:
            self.scanning = True
            log("firmware: scan start")
        elif ftype == link.SCAN_STOP:
            self.scanning = False
            log("firmware: scan stop")
        elif ftype == link.CONNECT:
            await self.connect(p[:6])
        elif ftype == link.DISCONNECT:
            await asyncio.sleep(0.02)
            self.drop_link(0x16, "firmware disconnected")
        elif not self.connected:
            log(f"firmware sent {link.NAMES.get(ftype)} while not connected - ignored")
        elif ftype == link.DISCOVER:
            await self.discover()
        elif ftype == link.READ:
            await self.read(struct.unpack("<H", p[:2])[0])
        elif ftype == link.WRITE:
            handle, with_rsp = struct.unpack("<HB", p[:3])
            await self.write(handle, bool(with_rsp), p[3:])
        elif ftype == link.MTU_REQ:
            (want,) = struct.unpack("<H", p[:2])
            await self.att_delay()
            self.mtu = max(23, min(want, 247))
            log(f"MTU {self.mtu} (asked {want})")
            self.send(link.EVT_MTU, struct.pack("<H", self.mtu))

    async def connect(self, addr):
        dev = next((d for d in self.devices if addr_bytes(d.addr) == addr), None)
        name = dev.name if dev else addr[::-1].hex(":")
        log(f"firmware: connect to {name}")
        if self.connected:
            log("  already connected - ignored")
            return
        if not dev or not dev.advertising or random.random() < self.scn["connect_fail_rate"]:
            await asyncio.sleep(1.0)
            log(f"  connect to {name} failed")
            self.send(link.EVT_DISCONNECTED, bytes([0x3E]))
            return
        await asyncio.sleep(random.uniform(0.1, 0.3))
        if not dev.advertising:
            self.send(link.EVT_DISCONNECTED, bytes([0x3E]))
            return
        dev.advertising = False
        self.connected = dev
        self.mtu = 23
        self.conn_handle = (self.conn_handle % 0x0EFF) + 1
        dev.on_connect()
        log(f"  connected to {dev.name} (conn {self.conn_handle})")
        self.send(link.EVT_CONNECTED, struct.pack("<H", self.conn_handle) + addr)

    async def discover(self):
        dev = self.connected
        for c in dev.chars:
            await asyncio.sleep(0.005)
            if self.connected is not dev:
                return
            self.send(link.EVT_CHAR, c.service + c.uuid +
                      struct.pack("<HHB", c.value_handle, c.cccd_handle, c.props))
        await self.att_delay()
        if self.connected is dev:
            log(f"discovery done ({len(dev.chars)} characteristics)")
            self.send(link.EVT_DISC_DONE, b"\x00")

    async def read(self, handle):
        dev = self.connected
        await self.att_delay()
        if self.connected is not dev:
            return
        c = dev.by_handle(handle)
        if not c:
            status, val = ATT_INVALID_HANDLE, b""
        elif handle == c.cccd_handle:
            status, val = ATT_OK, struct.pack("<H", int(c.notifying))
        elif not c.props & PROP_READ:
            status, val = ATT_READ_NOT_PERMITTED, b""
        else:
            status, val = ATT_OK, c.read()[: self.mtu - 1]
        self.send(link.EVT_READ_RSP, struct.pack("<HB", handle, status) + val)

    async def write(self, handle, with_rsp, data):
        dev = self.connected
        await self.att_delay()
        if self.connected is not dev:
            return
        c = dev.by_handle(handle)
        status = ATT_OK
        if not c:
            status = ATT_INVALID_HANDLE
        elif len(data) > self.mtu - 3:
            status = ATT_INVALID_LEN
        elif handle == c.cccd_handle:
            if len(data) != 2:
                status = ATT_INVALID_LEN
            else:
                c.notifying = bool(data[0] & 1)
                log(f"notifications {'on' if c.notifying else 'off'} for handle {c.value_handle}")
        elif not c.props & PROP_WRITE or not c.write or not with_rsp:
            status = ATT_WRITE_NOT_PERMITTED
        if with_rsp:
            self.send(link.EVT_WRITE_RSP, struct.pack("<HB", handle, status))
        if status == ATT_OK and c and handle == c.value_handle and c.write:
            c.write(bytes(data))

    # -- background loops --------------------------------------------------

    async def adv_loop(self):
        while True:
            await asyncio.sleep(0.2)
            if not self.scanning:
                continue
            for d in self.devices:
                if d.advertising and random.random() > 0.15:
                    data = d.adv_data()
                    rssi = max(-100, min(-30, d.rssi + int(random.gauss(0, 3))))
                    self.send(link.EVT_ADV, addr_bytes(d.addr) + struct.pack("<bB", rssi, len(data)) + data)

    async def telemetry_loop(self):
        last_level = None
        while True:
            now = time.monotonic()
            hz = 200 if self.storm_until > now else self.scn["telemetry_hz"]
            await asyncio.sleep(1 / hz * random.uniform(0.9, 1.1))
            pack = self.pack
            pack.step()
            if self.connected is not pack:
                continue
            level = round(pack.soc * 100)
            if level != last_level:
                last_level = level
                self.notify(pack.batt_level, bytes([level]))
            if not pack.telem.notifying:
                continue
            corrupt = self.corrupt_until > now or random.random() < self.scn["crc_error_rate"]
            frame = pack.telemetry_frame(corrupt)
            if random.random() < self.scn["drop_rate"]:
                self.stats["telemetry_dropped"] += 1
                continue
            self.stats["telemetry_sent"] += 1
            self.stats["crc_corrupted"] += corrupt
            self.notify(pack.telem, frame)

    async def chaos_loop(self):
        scn = self.scn
        next_drop = next_reboot = None
        while True:
            await asyncio.sleep(0.25)
            now = time.monotonic()
            if scn["link_drop_every_s"] and self.connected:
                next_drop = next_drop or now + random.uniform(*scn["link_drop_every_s"])
                if now >= next_drop:
                    next_drop = None
                    self.drop_link()
            if scn["reboot_every_s"]:
                next_reboot = next_reboot or now + random.uniform(*scn["reboot_every_s"])
                if now >= next_reboot:
                    next_reboot = None
                    await self.reboot_pack()

    async def stats_loop(self):
        while True:
            await asyncio.sleep(10)
            if self.connected:
                s = self.stats
                log(f"telemetry sent={s['telemetry_sent']} dropped={s['telemetry_dropped']} "
                    f"bad_crc={s['crc_corrupted']} mtu={self.mtu}")

    # -- servers -----------------------------------------------------------

    async def serve_firmware(self, reader, writer):
        if self.writer:
            log("second firmware connection refused")
            writer.close()
            return
        log("firmware attached")
        self.writer = writer
        try:
            while True:
                ftype, payload = await link.read_frame(reader)
                await self.handle(ftype, payload)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            log("firmware detached")
            self.writer = None
            self.scanning = False
            if self.connected:
                self.connected.on_disconnect()
                self.connected.advertising = True
                self.connected = None
                self.mtu = 23

    async def serve_ctl(self, reader, writer):
        line = (await reader.readline()).decode().split()
        reply = self.control(line)
        writer.write((reply + "\n").encode())
        await writer.drain()
        writer.close()

    def control(self, args):
        if not args:
            return "commands: drop | reboot [s] | storm [s] | corrupt [s] | silent [s] | status"
        cmd = args[0]
        secs = float(args[1]) if len(args) > 1 else None
        now = time.monotonic()
        log(f"control: {' '.join(args)}")
        if cmd == "drop":
            self.drop_link()
        elif cmd == "reboot":
            self.spawn(self.reboot_pack(secs))
        elif cmd == "storm":
            self.storm_until = now + (secs or 5)
        elif cmd == "corrupt":
            self.corrupt_until = now + (secs or 5)
        elif cmd == "silent":
            self.silent_until = now + (secs or 10)
        elif cmd != "status":
            return f"unknown command {cmd}"
        return json.dumps({"firmware_attached": bool(self.writer), "scanning": self.scanning,
                           "connected": self.connected.name if self.connected else None,
                           "mtu": self.mtu, "soc": round(self.pack.soc * 100),
                           "fault_latched": self.pack.fault_latched,
                           "charge_limit": self.pack.charge_limit, **self.stats})


def load_scenario(name):
    scn = dict(DEFAULT_SCENARIO)
    if name and name != "default":
        path = name if name.endswith(".json") else os.path.join(HERE, "scenarios", name + ".json")
        with open(path) as f:
            scn.update(json.load(f))
    return scn


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default=os.environ.get("SCENARIO", "default"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("EMU_PORT", 7700)))
    ap.add_argument("--ctl-port", type=int, default=int(os.environ.get("EMU_CTL_PORT", 7701)))
    ap.add_argument("--host", default=os.environ.get("EMU_BIND", "0.0.0.0"))
    args = ap.parse_args()

    scn = load_scenario(args.scenario)
    if scn["seed"] is not None:
        random.seed(scn["seed"])
    world = World(scn)
    await asyncio.start_server(world.serve_firmware, args.host, args.port)
    await asyncio.start_server(world.serve_ctl, args.host, args.ctl_port)
    log(f"scenario '{args.scenario}', radio on :{args.port}, control on :{args.ctl_port}")
    await asyncio.gather(world.adv_loop(), world.telemetry_loop(), world.chaos_loop(), world.stats_loop())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
