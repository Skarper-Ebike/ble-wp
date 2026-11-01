<p align="center"><img src="docs/img/banner.svg" alt="Break out the pack: a firmware escape room" width="100%"></p>

A rider is stranded. After a bad firmware update their battery pack locked
itself into maintenance mode, and it only opens up one room at a time. You're
writing the firmware for the bike's main controller (a BLE central) that talks
its way through five rooms and frees the pack.

Everything runs on your laptop: the firmware on FreeRTOS (POSIX port), the
pack as an emulator next to it. Use AI tools as much as you like. We care
about what you build and how you steer it, not how much you type.

---

## 🧰 1. Setup (before the interview, ~5 min)

**Install Docker.** Copy the block for your machine.

<details open>
<summary><b>macOS</b> (Homebrew)</summary>

```sh
brew install --cask docker
open -a Docker
```

Wait for the whale icon in the menu bar to stop animating. No Homebrew? Get
[Docker Desktop](https://www.docker.com/products/docker-desktop/) instead.
</details>

<details>
<summary><b>Linux</b> (Ubuntu / Debian)</summary>

```sh
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER && newgrp docker
```
</details>

<details>
<summary><b>Windows</b> (admin PowerShell, then use WSL)</summary>

```powershell
wsl --install
winget install -e --id Docker.DockerDesktop
```

Restart, start Docker Desktop, then run everything below in your WSL (Ubuntu) terminal.
</details>

**Get the repo and build it once.** This downloads FreeRTOS and GoogleTest, so
it's slow the first time and quick on the day.

```sh
git clone https://github.com/skarper-ebike/ble-wp.git && cd ble-wp
docker compose build
docker build -f Dockerfile.test -t ble-wp-test .
docker compose up
```

✅ You should see the firmware find three devices and the pack say it has no
mission token. That's right: it stays locked until the interview. Ctrl+C to stop.

---

## 🚀 2. Start the mission (on the day)

Your interviewer gives you a token. **The clock starts when it's issued** and
keeps running whatever you do to the containers.

Open three terminals in the `ble-wp` folder.

**Terminal 1 · the pack.** Put your token in, start it once, leave it running.
Its log is your mission console: room banners, clock, what the pack sees.

```sh
echo MISSION=PASTE-TOKEN > .env
docker compose down
docker compose up -d --build emu && docker compose logs -f emu
```

**Terminal 2 · your firmware.** Run it again (Ctrl+C, ↑, Enter) after every change.

```sh
docker compose up --build --no-deps fw
```

**Terminal 3 · mission control.**

```sh
docker compose exec emu python3 ctl.py score
```

| Command | What it does |
|---|---|
| `ctl.py score` | Rooms opened, clock, hints taken |
| `ctl.py hint` | Next clue for your current room. **Costs 5 minutes** |

**Tests** (GoogleTest / GMock):

```sh
docker build -f Dockerfile.test -t ble-wp-test . && docker run --rm ble-wp-test
```

`BLE_TRACE=1 docker compose up --build --no-deps fw` prints every radio frame.
The pack also logs every frame to `logs/transcript.jsonl`.

<details>
<summary>Without Docker (Linux: CMake ≥ 3.22, GCC, Python ≥ 3.8)</summary>

```sh
MISSION=PASTE-TOKEN python3 emulator/emulator.py                      # terminal 1
cmake -S . -B build && cmake --build build && ./build/ble_wp           # terminal 2
python3 emulator/ctl.py score                                          # terminal 3
cmake -S . -B build-test -DBUILD_APP=OFF -DBUILD_TESTING=ON && cmake --build build-test && ctest --test-dir build-test
```
</details>

---

## 🔒 3. The rooms

<p align="center"><img src="docs/img/rooms.svg" alt="Five rooms: 1 find it, 2 listen, 3 talk, 4 survive, 5 escape" width="100%"></p>

Everything you need is in [`docs/ICD.md`](docs/ICD.md).

| | Room | Your job |
|---|---|---|
| 📡 | **1 · Find it** | Find the right pack in the advertising data, connect, discover its services and unlock it. |
| 〰️ | **2 · Listen** | Subscribe to the telemetry characteristic and decode it. It carries the phrase that opens this room. |
| ⌨️ | **3 · Talk** | Use the command channel to find out which cell is failing, and get its diagnostic code. |
| ⚡ | **4 · Survive** | Keep your firmware talking to the pack, whatever happens. The pack gives you the code when it's satisfied. |
| 🚪 | **5 · Escape** | Download the pack's event log. Its CRC-32 is the last code. Stretch goal. |

A room is open when the mission console says so. The pack reboots after each
of the first three.

**Throughout:** keep the logic you'd want to test (decoders, the state machine) in `application_core` and write unit tests for it as you go.

---

## 🗝️ 4. Opening a room

Your **firmware** hands in each code. There's no other way in: you can't type
codes, and the interviewer can't either.

```mermaid
sequenceDiagram
    participant FW as Your firmware
    participant P as PACK-3A1F
    Note over FW: found the code for room N
    FW->>P: write Unlock = [N] + "CODE"
    alt right code
        P-->>FW: write response 0x00
        P-->>FW: notify "ROOM N OPEN"
        P--xFW: reboots into room N+1 (disconnect 0x13)
        FW->>P: reconnect, rediscover, carry on
    else wrong code
        P-->>FW: write response 0x80 + notify "WRONG CODE"
        Note over P: 3 wrong in a row = locked out for 30 s
    end
```

When a room opens, the mission console tells you:

```
###############################################################################
# ROOM 2 OPEN  (31:07 elapsed, 48:52 left)                                    #
# Telemetry link verified. After the next reboot the pack will take commands. #
###############################################################################
```

---

## 📜 Rules

- Your code goes in `src/application/` (and `tests/`). Don't change `src/ble/` or `emulator/`.
- Treat the pack as hardware you can't see inside. Don't read the emulator's code.
- Logic you want to unit test goes in `application_core` (no FreeRTOS in it). Task and queue glue goes in the firmware target.
- Commit as you go. We'll read the history.
- `CLAUDE.md`, `AGENTS.md` and `.claude/` set the rules for your AI tools. Leave them as they are.

## ⏱️ The session (~2 h)

| | |
|---|---|
| 10 min | We walk through this README and give you your token |
| 80 min | You build. The clock is on the pack |
| 30 min | You walk us through your code and decisions, then one small change live |

**Half your score is the rooms, half is the code and how you explain it.**
Nobody escapes every time: three rooms done well is a good result. Don't rush.

## 🗺️ Where to find things

| What | Where |
|---|---|
| The pack's BLE interface and the rooms | [`docs/ICD.md`](docs/ICD.md) |
| The BLE API you build on, and its rules | [`src/ble/ble_central.h`](src/ble/ble_central.h) |
| Starter application | [`src/application/app.c`](src/application/app.c) |
| BLE mock for unit tests | [`tests/mocks/ble_mock.h`](tests/mocks/ble_mock.h) |

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
