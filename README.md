# ble-wp

Hands-on firmware exercise. You write the BLE application for an MCU that
talks to a smart battery pack. Both are emulated, so everything runs on your
laptop: the firmware on FreeRTOS (POSIX port), the pack as a Python process
next to it.

Use AI tools as much as you like. We care about what you build and how you
steer it, not how much you type.

## Setup

You need **Docker** with Compose. That's it.

Or, on Linux without Docker: CMake ≥ 3.22, GCC, Python ≥ 3.8 (no packages).

Before the interview, check it runs:

```
docker compose up --build
```

You should see the firmware find three devices. Ctrl+C to stop.

## Run

```
docker compose up --build                       # emulator + firmware, logs interleaved
SCENARIO=flaky_link docker compose up --build   # with a fault scenario (see emulator/scenarios/)
```

To break the pack on purpose, keep the above running and, in a second terminal:

```
docker compose exec emu python3 ctl.py reboot   # or: drop, storm, corrupt, silent, status
```

Tests (GoogleTest / GMock):

```
docker build -f Dockerfile.test -t ble-wp-test . && docker run --rm ble-wp-test
```

Without Docker:

```
python3 emulator/emulator.py &
cmake -S . -B build && cmake --build build && ./build/ble_wp
cmake -S . -B build-test -DBUILD_APP=OFF -DBUILD_TESTING=ON && cmake --build build-test && ctest --test-dir build-test
```

`BLE_TRACE=1` prints every radio frame. The emulator logs every frame to
`logs/transcript.jsonl`.

## The task

The MCU is a bike's main controller (BLE central). The pack is a BLE
peripheral. Get as far as you can, in order:

1. **Connect.** Find the pack and connect to it. Other devices are advertising too.
2. **Telemetry.** Read the device info and battery level, subscribe to
   telemetry, decode it. Drop bad frames, count lost ones, print a one-line
   status each second.
3. **Commands.** Implement the command channel: GET_INFO, GET_CELLS,
   SET_CHARGE_LIMIT, CLEAR_FAULTS. Handle slow, failed and missing responses.
4. **Stay up.** Survive link loss and pack reboots: reconnect on its own,
   recover its state, never crash or hang. Try the scenarios and `ctl.py`.
5. **Stretch.** Download the pack's event log and verify its CRC.

Tests for the parts that matter, as you go.

Rules:

- Your code goes in `src/application/` (and `tests/`). Don't change `src/ble/` or `emulator/`.
- Logic you want to unit test goes in `application_core` (no FreeRTOS in it). Task and queue glue goes in the firmware target.
- Commit as you go. We'll read the history.

## The session (~2 h)

- 10 min: we walk through this README, you ask questions.
- 80 min: you build. At some point we'll break the pack on purpose.
- 30 min: you walk us through your code and decisions, then one small change live.

Nobody finishes everything. We'd rather see 1–4 solid than 1–5 shaky.

## Where to find things

| What | Where |
|---|---|
| The pack's BLE interface (services, telemetry format, commands) | `docs/ICD.md` |
| The BLE API you build on, and its rules | `src/ble/ble_central.h` |
| Starter application | `src/application/app.c` |
| BLE mock for unit tests | `tests/mocks/ble_mock.h` |
| Fault scenarios | `emulator/scenarios/` |
| Live fault injection | `emulator/ctl.py` |

```
├── docs/ICD.md            pack interface spec
├── src/
│   ├── main.c             scheduler bring-up
│   ├── application/       your code
│   └── ble/               BLE API + simulated stack (given)
├── tests/                 GTest + BLE mock
├── emulator/              the fake pack (given)
├── Dockerfile             firmware
├── Dockerfile.test        unit tests
└── docker-compose.yml     firmware + emulator
```
