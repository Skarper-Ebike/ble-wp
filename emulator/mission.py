"""Mission token, per-mission secrets, hints and saved progress.

A token encodes a 16-bit seed and the minute the mission was issued, plus a
check value. Everything a candidate has to find is derived from the seed, so
codes differ per candidate. The clock runs from the issue time on the wall
clock: restarting the emulator never pauses or resets it.
"""

import hashlib
import json
import os
import random
import time
import zlib

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"   # Crockford base32
CODE_CHARS = "ABCDEFGHJKMNPQRSTVWXYZ23456789"   # no 0/O, 1/I/L
EPOCH = 1767225600                              # 2026-01-01T00:00:00Z
DURATION_S = int(os.environ.get("MISSION_MINUTES", "80")) * 60
HINT_PENALTY_S = 5 * 60
COMPANY_ID = 0xFFFF                              # Bluetooth SIG "test" company ID
DECOY_COMPANY_ID = 0x0059

ROOM_NAMES = {1: "find it", 2: "listen", 3: "talk", 4: "survive", 5: "escape"}

WORDS = ["AMBER", "CEDAR", "DELTA", "EMBER", "FROST", "GAMMA", "IVORY", "JOULE", "LUNAR", "MAPLE",
         "NORTH", "OCEAN", "PIXEL", "QUARK", "RIVER", "SOLAR", "TIGER", "ULTRA", "VAPOR", "WATTS"]

STORY = {
    1: "Pack identified. It's rebooting into maintenance mode: telemetry and battery service come online.",
    2: "Telemetry link verified. After the next reboot the pack will take commands.",
    3: "Fault cleared and the weak cell isolated. The pack is unstable - brace for the storm.",
    4: "Your firmware rode out the storm. The event log is unlocked.",
    5: "PACK RELEASED. The rider is on their way home.",
}

HINTS = {
    1: ["While locked, the pack doesn't advertise a service UUID. Look at every AD structure in the advert, not just the name.",
        "AD type 0xFF is manufacturer-specific data: a 2-byte company ID (little-endian), then payload. Check the company ID the ICD gives.",
        "Payload is the current room (1 byte), then the 6-character code. Connect, discover, write [0x01] + code to Unlock."],
    2: ["Telemetry carries more than measurements. Look at the field the old ICD called reserved.",
        "Each valid frame carries 2 characters of a 12-character phrase. The slot is seq mod 6.",
        "Frames with a bad CRC carry junk in that field. Only trust frames that pass the CRC, then write [0x02] + phrase."],
    3: ["The pack refuses most commands at first. Read the status codes it sends back.",
        "Status 6 is a latched fault: clear it. Then find the unhealthy cell from GET_CELLS.",
        "The weak cell sags lowest under load. GET_DIAG on that cell needs a bigger MTU; the code follows the word DIAG."],
    4: ["Nothing to decode in this room. The pack is checking that your firmware recovers on its own.",
        "After each drop or reboot the pack wants telemetry flowing again and a command answered within 8 s of reconnecting.",
        "After three good recoveries the code appears on Unlock (notify, or read it). Write it back as room 4."],
    5: ["The event log is open. GET_INFO says how big it is.",
        "LOG_READ returns at most MTU - 11 bytes per call. Track the offset and resume after a drop.",
        "The code is the CRC-32 (as in zlib) of the whole log, as 8 uppercase hex digits."],
}


# ---------------------------------------------------------------- token

def _check(value40: int) -> int:
    return zlib.crc32(value40.to_bytes(5, "little")) & 0x3FF


def encode_token(seed: int, issued_ts: float) -> str:
    minute = int((issued_ts - EPOCH) // 60)
    value = ((seed & 0xFFFF) << 24) | (minute & 0xFFFFFF)
    full = (value << 10) | _check(value)
    chars = "".join(ALPHABET[(full >> (5 * i)) & 31] for i in reversed(range(10)))
    return chars[:5] + "-" + chars[5:]


def decode_token(token: str):
    """Returns (seed, issued_ts). Raises ValueError on a typo."""
    s = token.strip().upper().replace("-", "").replace("O", "0").replace("I", "1").replace("L", "1")
    if len(s) != 10 or any(c not in ALPHABET for c in s):
        raise ValueError("a token is 10 characters, like 7Q4KM-2XDA9")
    full = 0
    for c in s:
        full = (full << 5) | ALPHABET.index(c)
    value, check = full >> 10, full & 0x3FF
    if _check(value) != check:
        raise ValueError("check digits don't match - typo?")
    seed, minute = value >> 24, value & 0xFFFFFF
    return seed, EPOCH + minute * 60


def new_seed() -> int:
    return random.SystemRandom().randrange(1, 0x10000)


# ---------------------------------------------------------------- secrets

class Secrets:
    """Everything derived from the seed. Same seed, same answers."""

    def __init__(self, seed: int):
        self.seed = seed
        rng = random.Random(hashlib.sha256(f"ble-wp:{seed}".encode()).digest())

        def code(n):
            return "".join(rng.choice(CODE_CHARS) for _ in range(n))

        self.adv_code = code(6)
        self.decoy_adv_code = code(6)
        w1, w2 = rng.sample(WORDS, 2)
        self.phrase = f"{w1}-{w2}{rng.randrange(10)}"      # 12 characters
        self.weak_cell = rng.randrange(10)                    # 0-based
        self.diag_codes = [code(8) for _ in range(10)]        # only the weak cell's one is accepted
        self.cell_ir = [rng.randrange(11, 15) for _ in range(10)]
        self.cell_ir[self.weak_cell] = rng.randrange(52, 64)
        self.log_seed = rng.randrange(1 << 30)
        self.skip_key = hashlib.sha256(f"skip:{seed}".encode()).hexdigest()[:6].upper()

    @property
    def diag_code(self):
        return self.diag_codes[self.weak_cell]


def make_log(log_seed: int) -> bytes:
    rng = random.Random(log_seed)
    lines = ["timestamp,event,detail"]
    t = 1760000000 + rng.randrange(86400 * 30)
    events = ["BOOT", "RIDE_START", "RIDE_END", "CHARGE_START", "CHARGE_END",
              "TEMP_WARN", "CELL_IMBALANCE", "FAULT_OC", "FAULT_CLEARED"]
    while len("\n".join(lines)) < 4000:
        t += rng.randint(60, 7200)
        lines.append(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(t))},{rng.choice(events)},"
                     f"soc={rng.randint(5, 100)};t={rng.randint(12, 48)}C")
    return ("\n".join(lines) + "\n").encode()


# ---------------------------------------------------------------- saved progress

def fmt(secs: float) -> str:
    secs = int(max(0, secs))
    return f"{secs // 60:02d}:{secs % 60:02d}"


class Mission:
    def __init__(self, token: str, path: str):
        self.token = token.strip().upper()
        self.seed, self.issued = decode_token(token)
        self.secrets = Secrets(self.seed)
        self.path = path
        self.state = {"token": self.token, "opened": {}, "skipped": [], "wrong": 0, "hints": []}
        try:
            with open(path) as f:
                saved = json.load(f)
            if saved.get("token") == self.token:
                self.state = saved
        except (OSError, ValueError):
            pass

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.state, f, indent=1)
        os.replace(tmp, self.path)

    @property
    def elapsed(self) -> float:
        return time.time() - self.issued

    @property
    def penalty(self) -> int:
        return HINT_PENALTY_S * len(self.state["hints"])

    @property
    def left(self) -> float:
        return DURATION_S - self.elapsed - self.penalty

    @property
    def room(self) -> int:
        """Current room: the first one not opened yet (6 = escaped)."""
        r = 1
        while str(r) in self.state["opened"]:
            r += 1
        return r

    def open_room(self, room: int, skipped=False):
        self.state["opened"][str(room)] = round(self.elapsed)
        if skipped:
            self.state["skipped"].append(room)
        self.save()

    def wrong_code(self):
        self.state["wrong"] += 1
        self.save()

    def take_hint(self) -> str:
        room = self.room
        if room > 5:
            return "You've escaped. No hints needed."
        taken = [h for h in self.state["hints"] if h["room"] == room]
        if len(taken) >= len(HINTS[room]):
            return f"No more hints for room {room}. Ask your interviewer."
        level = len(taken)
        self.state["hints"].append({"room": room, "at": round(self.elapsed)})
        self.save()
        return f"Hint {level + 1}/3 for room {room} (+5:00): {HINTS[room][level]}"

    def score(self, extra="") -> str:
        lines = [f"MISSION {self.token}   elapsed {fmt(self.elapsed)}   left {fmt(self.left)}"]
        for r in range(1, 6):
            at = self.state["opened"].get(str(r))
            hints = sum(1 for h in self.state["hints"] if h["room"] == r)
            note = f" ({hints} hint{'s' if hints != 1 else ''})" if hints else ""
            if r in self.state["skipped"]:
                status = f"SKIPPED {fmt(at)}"
            elif at is not None:
                status = f"OPEN    {fmt(at)}"
            elif r == self.room:
                status = "in progress" + (f", {extra}" if extra and r == 4 else "")
            else:
                status = "locked"
            lines.append(f"  room {r}  {ROOM_NAMES[r]:<8} {status}{note}")
        n = len(self.state["hints"])
        lines.append(f"wrong codes {self.state['wrong']}, hints {n} (+{fmt(n * HINT_PENALTY_S)}), "
                     f"skips {len(self.state['skipped'])}")
        return "\n".join(lines)
