# ble-wp: break out the pack

A rider is stranded. After a bad firmware update their battery pack locked
itself into maintenance mode, and it only opens up one room at a time. You're
writing the firmware for the bike's main controller (a BLE central) that talks
its way through five rooms and releases the pack.

Everything runs on your laptop: the firmware on FreeRTOS (POSIX port), the
pack as an emulator next to it. Use AI tools as much as you like. We care
about what you build and how you steer it, not how much you type.

## Setup

You need **Docker** with Compose. (Or, on Linux without Docker: CMake ≥ 3.22,
GCC, Python ≥ 3.8.)

Before the interview, check it runs:

```
docker compose up --build
```

The firmware should find three devices. The pack stays locked: that's expected
until you have a mission token. Ctrl+C to stop.

## Start the mission

Your interviewer gives you a token at the start of the session. The clock
starts when it's issued and keeps running whatever you do to the containers.

```
echo MISSION=7Q4KM-2XDA9 > .env
```

(with your token). Then use three terminals, all in the repo folder:

**Terminal 1, the pack.** Start it once and leave it running. Its log is the
mission console: room banners, clock, what the pack sees.

```
docker compose up -d emu && docker compose logs -f emu
```

**Terminal 2, your firmware.** Ctrl+C and run it again after every change.

```
docker compose up --build --no-deps fw
```

**Terminal 3, mission control.**

```
docker compose exec emu python3 ctl.py score
docker compose exec emu python3 ctl.py hint
```

`score` shows your rooms and the clock. `hint` gives the next clue for your
current room and costs 5 minutes. You can also break the pack yourself:
`drop`, `reboot`, `storm`, `corrupt`, `silent` (optional duration in seconds,
e.g. `ctl.py silent 5`).

Tests (GoogleTest / GMock):

```
docker build -f Dockerfile.test -t ble-wp-test . && docker run --rm ble-wp-test
```

Without Docker: `MISSION=<token> python3 emulator/emulator.py` in terminal 1,
`cmake -S . -B build && cmake --build build && ./build/ble_wp` in terminal 2,
`python3 emulator/ctl.py score` in terminal 3. Tests:
`cmake -S . -B build-test -DBUILD_APP=OFF -DBUILD_TESTING=ON && cmake --build build-test && ctest --test-dir build-test`.

`BLE_TRACE=1` prints every radio frame. The pack logs every frame to
`logs/transcript.jsonl`.

## The rooms

1. **Find it.** Several devices are advertising. Find the stranded pack, connect and unlock it.
2. **Listen.** Telemetry comes online. Decode it properly and it gives up a phrase.
3. **Talk.** The command channel opens, but the pack is in a bad way. Get it to tell you which cell is failing.
4. **Survive.** The pack gets unstable. Your firmware has to recover on its own, every time.
5. **Escape.** Pull the pack's event log and prove you have all of it.

Everything you need is in `docs/ICD.md`. Codes are only accepted when your
firmware writes them to the pack. Rooms open in order.

Rules:

- Your code goes in `src/application/` (and `tests/`). Don't change `src/ble/` or `emulator/`.
- Treat the pack as hardware you can't see inside. Don't read the emulator's code.
- Logic you want to unit test goes in `application_core` (no FreeRTOS in it). Task and queue glue goes in the firmware target.
- Commit as you go. We'll read the history.
- `CLAUDE.md`, `AGENTS.md` and `.claude/` set the rules for your AI tools. Leave them as they are.

## The session (~2 h)

- 10 min: we walk through this README and give you your token.
- 80 min: you build. The clock is on the pack.
- 30 min: you walk us through your code and decisions, then one small change live.

Half your score is the rooms, half is the code and how you explain it. Nobody
escapes every time: three rooms done well is a good result. Don't rush.

## Where to find things

| What | Where |
|---|---|
| The pack's BLE interface and the rooms | `docs/ICD.md` |
| The BLE API you build on, and its rules | `src/ble/ble_central.h` |
| Starter application | `src/application/app.c` |
| BLE mock for unit tests | `tests/mocks/ble_mock.h` |
| Mission control | `emulator/ctl.py` |

```
├── docs/ICD.md            pack interface spec
├── src/
│   ├── main.c             scheduler bring-up
│   ├── application/       your code
│   └── ble/               BLE API + simulated stack (given)
├── tests/                 GTest + BLE mock
├── emulator/              the pack (given, don't read it)
├── Dockerfile             firmware
├── Dockerfile.test        unit tests
└── docker-compose.yml     firmware + emulator
```
