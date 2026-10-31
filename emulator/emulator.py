#!/usr/bin/env python3
"""Fake BLE world for the firmware: a locked battery pack (PACK-3A1F) plus two decoys.

The firmware's sim driver connects over TCP (default :7700). The control port
(default :7701, see ctl.py) takes fault injection and mission commands.

Usage: MISSION=<token> python3 emulator.py [--scenario NAME]
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
import mission as M

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


def banner(lines) -> None:
    width = max(len(l) for l in lines) + 4
    print("\n" + "#" * width, flush=True)
    for l in lines:
        print(f"# {l.ljust(width - 4)} #", flush=True)
    print("#" * width + "\n", flush=True)


PROP_READ, PROP_WRITE, PROP_NOTIFY = 0x02, 0x08, 0x10
ATT_OK, ATT_INVALID_HANDLE, ATT_READ_NOT_PERMITTED, ATT_WRITE_NOT_PERMITTED, ATT_INVALID_LEN = 0, 1, 2, 3, 0x0D
UNLOCK_WRONG, UNLOCK_LOCKED_OUT, UNLOCK_WRONG_ROOM, UNLOCK_NO_MISSION = 0x80, 0x81, 0x82, 0x83

PACK_SVC = "8a1c0000-6b2e-4d8f-9a31-5c7e0f2b4d10"
PACK_TELEM = "8a1c0001-6b2e-4d8f-9a31-5c7e0f2b4d10"
PACK_CMD = "8a1c0002-6b2e-4d8f-9a31-5c7e0f2b4d10"
PACK_RSP = "8a1c0003-6b2e-4d8f-9a31-5c7e0f2b4d10"
PACK_UNLOCK = "8a1c0010-6b2e-4d8f-9a31-5c7e0f2b4d10"

ST_OK, ST_UNKNOWN, ST_BAD_LEN, ST_RANGE, ST_BUSY, ST_TOO_LARGE, ST_FAULT, ST_LOCKED = range(8)
OP_GET_INFO, OP_GET_CELLS, OP_SET_LIMIT, OP_CLEAR, OP_DIAG, OP_LOG = 0x01, 0x02, 0x03, 0x04, 0x05, 0x10

STORM_RECOVERY_WINDOW_S = 8
STORM_RECOVERIES_NEEDED = 3

DEFAULT_SCENARIO = {
    "telemetry_hz": 10,
    "drop_rate": 0.02,           # fraction of telemetry notifications lost
    "crc_error_rate": 0.03,      # fraction of telemetry frames with a bad CRC (and junk phrase chars)
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


def ad_field(ad_type: int, data: bytes) -> bytes:
    return bytes([len(data) + 1, ad_type]) + data


# ---------------------------------------------------------------- devices

class Device:
    def __init__(self, world, addr, name, rssi):
        self.world, self.addr, self.name, self.rssi = world, addr, name, rssi
        self.advertising = True
        self.rebooting = False
        self.chars = []

    def adv_data(self) -> bytes:
        return ad_field(0x09, self.name.encode())

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


class DecoyPack(Device):
    """Looks like a pack (same name pattern, manufacturer data) but from another vendor."""

    def __init__(self, world, code):
        super().__init__(world, "C0:FF:EE:00:77:C0", "PACK-77C0", -71)
        self.code = code
        near_miss = "8a1c0000-6b2e-4d8f-9a31-5c7e0f2b4d11"
        self.chars = dis_chars("Northwind", "PK-10S-400", "NW77C0", "1.0.3")
        self.chars.append(Char(uuid128(near_miss), uuid128("8a1c0001-6b2e-4d8f-9a31-5c7e0f2b4d11"),
                               PROP_READ | PROP_NOTIFY, read=lambda: b"\x00"))
        assign_handles(self.chars, 1)

    def adv_data(self) -> bytes:
        mfr = struct.pack("<H", M.DECOY_COMPANY_ID) + bytes([1]) + self.code.encode()
        return ad_field(0x09, self.name.encode()) + ad_field(0xFF, mfr)


class HeartRate(Device):
    def __init__(self, world):
        super().__init__(world, "D4:12:9B:00:00:42", "HRM-0042", -80)
        self.chars = dis_chars("Northwind", "HRM-2", "NW0042", "1.0.3")
        self.chars.append(Char(uuid16(0x180D), uuid16(0x2A37), PROP_NOTIFY))
        assign_handles(self.chars, 1)

    def adv_data(self) -> bytes:
        return ad_field(0x09, self.name.encode()) + ad_field(0x03, struct.pack("<H", 0x180D))


class Pack(Device):
    FW = (2, 4, 1)
    CELLS = 10
    CAPACITY_WH = 500
    CAPACITY_AH = 13.9
    OFFSETS_MV = [0, 3, -2, 5, -4, 1, -1, 2, 4]   # healthy cells; the weak one is added per mission

    def __init__(self, world):
        super().__init__(world, "C0:FF:EE:00:3A:1F", "PACK-3A1F", -58)
        sec = world.mission.secrets if world.mission else M.Secrets(0)
        self.secrets = sec
        self.cell_offsets = list(self.OFFSETS_MV)
        self.cell_offsets.insert(sec.weak_cell, -38)
        self.log_blob = M.make_log(sec.log_seed)
        self.soc = 0.87
        self.charging = False
        self.temp = 24.0
        self.charge_limit = 100
        self.fault_latched = True   # left over from the previous ride
        self.cmd_busy = False
        self.storm_code = None      # room 4 code, made up at runtime
        self.fail_streak = 0
        self.locked_until = 0.0

        svc = uuid128(PACK_SVC)
        self.unlock = Char(svc, uuid128(PACK_UNLOCK), PROP_READ | PROP_WRITE | PROP_NOTIFY,
                           read=self._unlock_status, write=self._on_unlock)
        self.batt_level = Char(uuid16(0x180F), uuid16(0x2A19), PROP_READ | PROP_NOTIFY,
                               read=lambda: bytes([round(self.soc * 100)]))
        self.telem = Char(svc, uuid128(PACK_TELEM), PROP_NOTIFY)
        self.cmd = Char(svc, uuid128(PACK_CMD), PROP_WRITE, write=self._on_command)
        self.rsp = Char(svc, uuid128(PACK_RSP), PROP_NOTIFY)
        self._boot()

    # -- mission state ---------------------------------------------------

    @property
    def room(self) -> int:
        return self.world.mission.room if self.world.mission else 1

    def _build_gatt(self):
        """The GATT table grows as rooms open. Handles move with it."""
        chars = dis_chars("Volta Packs Ltd", "PK-10S-500", "VP2311003A1F", ".".join(map(str, self.FW)))
        if self.room >= 2:
            chars.append(self.batt_level)
        chars.append(self.unlock)
        if self.room >= 2:
            chars.append(self.telem)
        if self.room >= 3:
            chars += [self.cmd, self.rsp]
        for c in chars:
            c.notifying = False
        assign_handles(chars, self.world.scn["handle_base"])
        self.chars = chars

    def _boot(self):
        self.seq = 0
        self.boot_t = time.monotonic()
        self.last_t = self.boot_t
        self.current = 0.0
        self.cmd_busy = False
        self._build_gatt()

    def adv_data(self) -> bytes:
        code = self.secrets.adv_code if self.world.mission else "------"
        mfr = struct.pack("<H", M.COMPANY_ID) + bytes([min(self.room, 6)]) + code.encode()
        return ad_field(0x09, self.name.encode()) + ad_field(0xFF, mfr)

    def _unlock_status(self) -> bytes:
        if not self.world.mission:
            return b"NO MISSION LOADED"
        r = self.room
        if r > 5:
            return b"ESCAPED"
        if r == 4 and self.storm_code:
            return f"ROOM 4 CODE {self.storm_code}".encode()
        return f"ROOM {r} LOCKED".encode()

    def _expected(self, room):
        sec = self.secrets
        return {1: sec.adv_code, 2: sec.phrase, 3: sec.diag_code, 4: self.storm_code,
                5: f"{zlib.crc32(self.log_blob):08X}"}.get(room)

    def _on_unlock(self, data: bytes):
        w = self.world
        if not w.mission:
            log("unlock attempt, but no mission is loaded (start with MISSION=<token>)")
            w.notify(self.unlock, b"NO MISSION LOADED")
            return UNLOCK_NO_MISSION
        if len(data) < 2:
            return ATT_INVALID_LEN
        room, code = data[0], data[1:].decode(errors="replace").strip().upper()
        if room != self.room:
            log(f"unlock for room {room}, but the pack is at room {self.room}")
            w.notify(self.unlock, f"AT ROOM {self.room}".encode())
            return UNLOCK_WRONG_ROOM
        if time.monotonic() < self.locked_until:
            w.notify(self.unlock, b"LOCKED OUT")
            return UNLOCK_LOCKED_OUT
        expected = self._expected(room)
        if expected is None or code != expected.upper():
            w.mission.wrong_code()
            self.fail_streak += 1
            log(f"room {room}: wrong code '{code}' ({self.fail_streak}/3)")
            if self.fail_streak >= 3:
                self.fail_streak = 0
                self.locked_until = time.monotonic() + 30
                log(f"room {room}: three wrong codes, locked out for 30 s")
            w.notify(self.unlock, b"WRONG CODE")
            return UNLOCK_WRONG
        self.fail_streak = 0
        w.open_room(room)
        w.notify(self.unlock, f"ROOM {room} OPEN".encode() if room < 5 else b"ESCAPED")
        if room <= 3:
            w.spawn(w.reboot_pack(2, why="into the next mode", delay=0.5))
        return ATT_OK

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
        for k, off in enumerate(self.cell_offsets):
            sag = self.current * self.secrets.cell_ir[k]
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
        slot = self.seq % 6
        maint = self.secrets.phrase[2 * slot:2 * slot + 2].encode()
        if corrupt:   # plausible-looking junk, only a CRC check catches it
            maint = bytes(random.choice(M.CODE_CHARS.encode()) for _ in range(2))
        body = struct.pack("<HHhhBbHHH", self.seq & 0xFFFF, v_mv, int(round(self.current * 100)),
                           int(round(power)), round(self.soc * 100), int(round(self.temp)),
                           min(cells), max(cells), flags) + maint
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
            return None
        req_id, op, ln = data[0], data[1], data[2]
        payload = data[3:]
        if op != OP_LOG:   # log reads come in bursts, keep the console readable
            log(f"command req_id={req_id} op=0x{op:02x} payload={payload.hex() or '-'}")
        if self.cmd_busy:
            self.world.spawn(self._respond(req_id, op, ST_BUSY, b"", 0.01))
            return None
        self.cmd_busy = True
        scn = self.world.scn
        lo, hi = scn["resp_delay_ms"]
        if self.world.slow_until > time.monotonic():
            lo, hi = 1000, 2500
        delay = random.uniform(lo, hi) / 1000
        if op == OP_LOG:
            delay /= 10   # log reads are quick
        if self.world.silent_until > time.monotonic() or random.random() < scn["resp_loss_rate"]:
            log(f"  (not answering req_id={req_id})")
            self.world.spawn(self._clear_busy(delay))
            return None
        if ln != len(payload):
            status, out = ST_BAD_LEN, b""
        else:
            status, out = self._execute(op, payload)
        self.world.spawn(self._respond(req_id, op, status, out, delay))
        return None

    def _execute(self, op, p):
        if op == OP_GET_INFO:
            if p:
                return ST_BAD_LEN, b""
            # log CRC is not reported in maintenance mode
            return ST_OK, struct.pack("<BBBBHII", *self.FW, self.CELLS, self.CAPACITY_WH, len(self.log_blob), 0)
        if op == OP_CLEAR:
            if p:
                return ST_BAD_LEN, b""
            if self.fault_latched:
                log("latched fault cleared")
            self.fault_latched = False
            return ST_OK, b""
        if op in (OP_GET_CELLS, OP_SET_LIMIT, OP_DIAG) and self.fault_latched:
            return ST_FAULT, b""
        if op == OP_GET_CELLS:
            if p:
                return ST_BAD_LEN, b""
            cells = self.cells_mv()
            return ST_OK, bytes([len(cells)]) + struct.pack(f"<{len(cells)}H", *cells)
        if op == OP_SET_LIMIT:
            if len(p) != 1:
                return ST_BAD_LEN, b""
            if not 50 <= p[0] <= 100:
                return ST_RANGE, b""
            self.charge_limit = p[0]
            return ST_OK, b""
        if op == OP_DIAG:
            if len(p) != 1:
                return ST_BAD_LEN, b""
            if not 1 <= p[0] <= self.CELLS:
                return ST_RANGE, b""
            k = p[0] - 1
            text = f"CELL {p[0]:02d} IR {self.secrets.cell_ir[k]}mOhm DIAG {self.secrets.diag_codes[k]}"
            return ST_OK, text.encode()
        if op == OP_LOG:
            if self.room < 5:
                return ST_LOCKED, b""
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
        if op != OP_LOG or status != ST_OK:
            log(f"  response req_id={req_id} op=0x{op:02x} status={status} len={len(payload)}")
        if status == ST_OK and self.rsp.notifying:
            self.world.storm_saw_command()
        self.world.notify(self.rsp, bytes([req_id, op, status, len(payload) & 0xFF]) + payload)


# ---------------------------------------------------------------- world

class World:
    def __init__(self, scn, mission):
        self.scn = scn
        self.mission = mission
        self.writer = None
        self.scanning = False
        self.connected = None
        self.conn_handle = 0
        self.mtu = 23
        self.tasks = set()
        self.storm_until = self.corrupt_until = self.silent_until = self.slow_until = 0.0
        self.stats = {"telemetry_sent": 0, "telemetry_dropped": 0, "crc_corrupted": 0}
        # room 4
        self.storm = {"recoveries": 0, "baseline": False, "faults": 0, "conn_at": None,
                      "cmd_ok": False, "ready": False, "next_fault": None}
        log_dir = os.environ.get("LOG_DIR", os.path.join(HERE, "..", "logs"))
        os.makedirs(log_dir, exist_ok=True)
        self.transcript = open(os.path.join(log_dir, "transcript.jsonl"), "a", buffering=1)
        self.pack = Pack(self)
        decoy_code = mission.secrets.decoy_adv_code if mission else "------"
        self.devices = [self.pack, DecoyPack(self, decoy_code), HeartRate(self)]

    # -- plumbing --------------------------------------------------------

    def spawn(self, coro):
        t = asyncio.ensure_future(coro)
        self.tasks.add(t)
        t.add_done_callback(self.tasks.discard)

    def record(self, direction, ftype, payload, **extra):
        self.transcript.write(json.dumps({"t": round(time.time(), 3), "dir": direction,
                                          "type": link.NAMES.get(ftype, ftype),
                                          "data": payload.hex(), **extra}) + "\n")

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

    # -- mission ---------------------------------------------------------

    def open_room(self, room, skipped=False):
        m = self.mission
        m.open_room(room, skipped)
        self.record("mission", "ROOM_OPEN", b"", room=room, skipped=skipped, elapsed=round(m.elapsed))
        if skipped:
            log(f"room {room} SKIPPED by the interviewer")
        else:
            banner([f"ROOM {room} OPEN  ({M.fmt(m.elapsed)} elapsed, {M.fmt(m.left)} left)", M.STORY[room]])
        if room == 3:
            self.storm.update(recoveries=0, baseline=False, faults=0, ready=False, next_fault=None)

    def storm_saw_command(self):
        if self.mission and self.pack.room == 4 and self.connected is self.pack:
            self.storm["cmd_ok"] = True

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

    async def reboot_pack(self, downtime=None, why="", delay=0.0):
        if delay:
            await asyncio.sleep(delay)
        downtime = self.scn["reboot_downtime_s"] if downtime is None else downtime
        log(f"pack rebooting {why} ({downtime:g} s off air)".replace("  ", " "))
        self.pack.rebooting = True
        self.pack.advertising = False
        if self.connected is self.pack:
            self.drop_link(0x13, "pack rebooting")
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
        if dev is self.pack:
            self.storm.update(conn_at=time.monotonic(), cmd_ok=False, ready=False)
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
        run_write = False
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
        else:
            run_write = True
        if run_write and c is getattr(dev, "unlock", None):
            status = c.write(bytes(data))          # unlock answers in the write response
            run_write = False
        if with_rsp:
            self.send(link.EVT_WRITE_RSP, struct.pack("<HB", handle, status))
        if run_write:
            c.write(bytes(data))

    # -- background loops --------------------------------------------------

    async def adv_loop(self):
        while True:
            await asyncio.sleep(0.2)
            if not self.scanning:
                continue
            for d in self.devices:
                if d.advertising and random.random() > 0.15:
                    data = d.adv_data()[:31]
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

    async def storm_loop(self):
        """Room 4: knock the link over and check the firmware recovers on its own."""
        st = self.storm
        while True:
            await asyncio.sleep(0.25)
            pack = self.pack
            if not self.mission or pack.room != 4 or pack.storm_code:
                continue
            now = time.monotonic()
            if self.connected is pack and not st["ready"] and pack.telem.notifying and st["cmd_ok"]:
                st["ready"] = True
                took = now - st["conn_at"]
                if not st["baseline"]:
                    st["baseline"] = True
                    log("storm: firmware is up. First hit in about 10 s.")
                elif took <= STORM_RECOVERY_WINDOW_S:
                    st["recoveries"] += 1
                    log(f"storm: recovered in {took:.1f} s ({st['recoveries']}/{STORM_RECOVERIES_NEEDED})")
                else:
                    log(f"storm: recovered, but took {took:.1f} s (over {STORM_RECOVERY_WINDOW_S} s, doesn't count)")
                self.record("mission", "STORM_RECOVERY", b"", took=round(took, 1), count=st["recoveries"])
                if st["recoveries"] >= STORM_RECOVERIES_NEEDED:
                    pack.storm_code = "".join(random.choice(M.CODE_CHARS) for _ in range(8))
                    log(f"storm over. Room 4 code is on Unlock: {pack.storm_code}")
                    self.notify(pack.unlock, f"ROOM 4 CODE {pack.storm_code}".encode())
                else:
                    st["next_fault"] = now + random.uniform(8, 12)
            if st["next_fault"] and now >= st["next_fault"] and self.connected is pack:
                st["next_fault"] = None
                kind = ("drop", "reboot", "slow")[st["faults"] % 3]
                st["faults"] += 1
                if kind == "drop":
                    self.drop_link(why="storm")
                elif kind == "reboot":
                    self.spawn(self.reboot_pack(4, why="(storm)"))
                else:
                    self.slow_until = now + 25
                    self.drop_link(why="storm, and the pack will answer slowly for a while")

    async def clock_loop(self):
        m = self.mission
        if not m:
            return
        last_mark = None
        while True:
            await asyncio.sleep(1)
            if m.room > 5:
                continue
            left = m.left
            mark = int(left // 300)
            if last_mark is not None and mark != last_mark and left > 0:
                log(f"mission clock: {M.fmt(left)} left, room {m.room} ({M.ROOM_NAMES[m.room]})")
            if left <= 0 and last_mark is not None and last_mark >= 0:
                banner(["TIME'S UP", "Keep going if you like - your interviewer will tell you what's next."])
            last_mark = mark

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
        line = (await reader.readline()).decode()
        args = line.split("#", 1)[0].split()   # zsh passes "# comments" through as arguments
        try:
            reply = self.control(args)
        except Exception as e:   # a bad control command must never take the pack down
            reply = f"error: {e}"
        writer.write((reply + "\n").encode())
        await writer.drain()
        writer.close()

    def control(self, args):
        if not args:
            return ("faults:  drop | reboot [s] | storm [s] | corrupt [s] | silent [s] | status\n"
                    "mission: score | hint")
        cmd = args[0]
        m = self.mission
        if cmd in ("score", "hint", "skip"):
            if not m:
                return "No mission loaded. Start the emulator with MISSION=<token> from your interviewer."
            log(f"control: {cmd}")
            if cmd == "score":
                extra = f"recoveries {self.storm['recoveries']}/{STORM_RECOVERIES_NEEDED}" if m.room == 4 else ""
                return m.score(extra)
            if cmd == "hint":
                text = m.take_hint()
                log(text)
                self.record("mission", "HINT", b"", room=m.room)
                return text
            # skip <room> <key>: interviewer only
            if len(args) != 3 or args[2].upper() != m.secrets.skip_key:
                return "skip needs the room and the interviewer's key"
            room = int(args[1])
            if room != m.room:
                return f"the pack is at room {m.room}"
            if room == 4:
                self.pack.storm_code = self.pack.storm_code or "SKIPPED"
            self.open_room(room, skipped=True)
            if room <= 3:
                self.spawn(self.reboot_pack(2, why="into the next mode"))
            return f"room {room} skipped"
        try:
            secs = float(args[1]) if len(args) > 1 else None
        except ValueError:
            return f"'{args[1]}' isn't a number of seconds"
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


def load_mission(token):
    if not token:
        log("no mission token: the pack stays locked. Start with MISSION=<token> to play.")
        return None
    log_dir = os.environ.get("LOG_DIR", os.path.join(HERE, "..", "logs"))
    os.makedirs(log_dir, exist_ok=True)
    try:
        m = M.Mission(token, os.path.join(log_dir, "mission.json"))
    except ValueError as e:
        log(f"mission token '{token}' isn't valid ({e}). The pack stays locked.")
        return None
    if m.room > 5:
        banner([f"MISSION {m.token}", "Already escaped. Well done."])
    else:
        banner([f"MISSION {m.token}",
                "A rider is stranded: their battery pack locked itself after a bad update.",
                "Get your firmware to break it out, room by room. See docs/ICD.md.",
                f"Started {M.fmt(m.elapsed)} ago. {M.fmt(m.left)} left. Now in room {m.room}."])
    return m


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
    world = World(scn, load_mission(os.environ.get("MISSION", "").strip()))
    await asyncio.start_server(world.serve_firmware, args.host, args.port)
    await asyncio.start_server(world.serve_ctl, args.host, args.ctl_port)
    log(f"scenario '{args.scenario}', radio on :{args.port}, control on :{args.ctl_port}")
    await asyncio.gather(world.adv_loop(), world.telemetry_loop(), world.chaos_loop(),
                         world.storm_loop(), world.clock_loop(), world.stats_loop())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
