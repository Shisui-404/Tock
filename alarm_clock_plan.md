# Alarm Clock — Cross-Platform Python CLI Application

## 1. Overview

**Brief:** *"Build an alarm clock as a Python CLI application. CLI only, no web UI,
no React, no database. There's no detailed spec; decide what to build with the
time you have."*

**What I'm building:** a dependency-free alarm clock that behaves the same on
**Windows, macOS and Linux** (including WSL). Alarms are managed via CLI
subcommands and fired by a long-running foreground `daemon` process. State
persists in a JSON file — deliberately a plain file rather than SQLite, to honour
the "no database" constraint.

**Guiding principles**
1. **Cross-platform is a first-class requirement**, not a polish item. Every
   OS-specific behaviour lives behind one small `compat/` layer with a
   guaranteed fallback, and CI runs on all three OSes.
2. **Always shippable.** Work is split into tiers (§6); the app is complete and
   submittable at the end of each tier.
3. **Stdlib only** at runtime and for tests — `python -m tock` and
   `python -m unittest` work on a fresh Python install with zero `pip install`.

## 2. Scope

**In scope**
- Add / list / remove / enable / disable alarms from the CLI
- One-off alarms (absolute or relative, e.g. `+10m`) and recurring alarms
  (daily, weekdays Mon–Fri, weekly on a chosen day)
- Optional label per alarm
- Foreground daemon that fires alarms on schedule
- Audible + visual alert with Stop / Snooze
- Persistence across restarts (JSON file, atomic + locked writes)
- Missed-alarm handling after sleep/shutdown (grace window)
- `doctor` command that reports what the app detected on this machine
- Python ≥ 3.10 on Windows 10/11, macOS 12+, mainstream Linux, WSL

**Out of scope**
- Web UI / GUI, database, phone notifications, music streaming
- Timezones (local system time only)
- Complex recurrence (iCal RRULE), calendar integration
- Waking a sleeping computer (not possible from a user-space CLI — documented)
- Installing as a background service (documented per OS as a manual extra)

## 3. Functional Requirements

| ID | Requirement | Tier |
|----|-------------|------|
| FR1 | `add HH:MM[:SS] [YYYY-MM-DD]` creates an alarm. Time-only → next occurrence (today if still ahead, else tomorrow). An explicit past date-time is rejected. 24-hour format, parsed without locale-dependent `strptime` directives. | 1 |
| FR2 | `add +Nm` / `+Nh` / `+Ns` creates a one-off relative alarm (makes demoing trivial: `tock add +1m`). | 1 |
| FR3 | `--label "text"` attaches a message shown when the alarm fires. | 1 |
| FR4 | `--repeat once\|daily` (default `once`). | 1 |
| FR5 | `--repeat weekdays\|weekly`, with `--day mon..sun` for weekly (defaults to the weekday of the first occurrence). Invalid combinations (`--date` with a repeat, `--day` without `weekly`) are rejected. | 2 |
| FR6 | `list` shows alarms sorted by next fire time: id, time, label, repeat, status (active / snoozed / disabled / done), countdown. | 1 (countdown: 2) |
| FR7 | `remove <id>` deletes an alarm; any unique id prefix is accepted. | 1 |
| FR8 | `toggle <id>` enables/disables; re-enabling recomputes the next fire time from now. | 2 |
| FR9 | `clear` purges completed one-off alarms. Fired one-offs are kept as `done` until then, so `list` shows what fired. | 2 |
| FR10 | `daemon` runs the scheduler in the foreground and picks up changes made from other terminals within ~1 s. | 1 |
| FR11 | Only one daemon may run per user; a second one exits with a clear message. | 1 |
| FR12 | On fire: sound (§5.6) + flashing banner with time and label. | 1 |
| FR13 | Alert prompt: `Enter` = stop, `s` + `Enter` = snooze. Rings until dismissed or `--ring-timeout` (default 5 min) elapses → auto-stop. | Stop: 1, Snooze: 2 |
| FR14 | Snooze (default 5 min, `--snooze N`) is persisted, so it survives a daemon restart. | 2 |
| FR15 | Missed alarms (machine asleep/off, daemon not running): if missed by ≤ `--grace` (default 15 min) fire immediately; otherwise skip, print a "missed" notice, and advance. A recurring alarm fires **at most once** no matter how many occurrences were missed. | 2 |
| FR16 | One-off alarms become `done` after firing; recurring alarms advance to their next occurrence. | 1 |
| FR17 | No TTY (e.g. `pythonw`, redirected output): no prompt; banner is logged, sound rings for `--ring-timeout`, then auto-stops. | 2 |
| FR18 | `doctor` prints OS, Python version, data directory, chosen sound backend, colour/Unicode support, TTY status, and whether a daemon is running. `doctor --sound` plays a 2-second test alarm. | 1 |
| FR19 | Optional desktop notification on fire (`--notify`): PowerShell toast / `osascript` / `notify-send`. | 3 |

## 4. Non-Functional Requirements

- **NFR1 Zero dependencies.** Stdlib only, runtime and tests. Python ≥ 3.10.
- **NFR2 Cross-platform parity.** Same commands, output and behaviour on
  Windows, macOS, Linux. OS differences are confined to `tock/compat/`.
- **NFR3 Graceful degradation.** Every platform feature has a fallback chain
  ending in something that always works (terminal bell, plain text, log-only).
  Missing features never crash the app; `doctor` explains what was chosen.
- **NFR4 Accuracy.** Alarms fire within ~1 s of schedule while the daemon is
  running. Each tick compares against the wall clock (`datetime.now()`), never
  accumulated sleep time, so suspend/resume and clock changes are handled.
- **NFR5 Data safety.** Writes are atomic and serialised by a file lock, so
  concurrent CLI + daemon writes never lose updates. A corrupt store is backed
  up (`alarms.json.corrupt-<timestamp>`), the user is warned, and the app
  starts empty.
- **NFR6 Encoding safety.** All file I/O uses explicit `encoding="utf-8"`.
  Console output falls back to ASCII when the terminal can't encode Unicode.
- **NFR7 Testability.** Clock, sleep, sound and input are injected, so the
  scheduler is unit-tested without real time passing or real audio.

## 5. Design

### 5.1 Architecture
```
┌────────────┐  store.update()   ┌────────────────────┐
│  CLI cmds  │ ────(locked)────► │ alarms.json        │
│ add/list/  │                   │ + alarms.lock      │
│ remove/... │                   └─────────┬──────────┘
└────────────┘                             │ reload on change (mtime_ns+size, each tick)
                                           ▼
┌──────────────┐   holds    ┌──────────────────────────┐
│ daemon.lock  │ ◄───────── │  daemon / scheduler loop │── store.update() (locked)
└──────────────┘            └────────────┬─────────────┘
                                         │ due alarm
                                         ▼
                            ┌──────────────────────────┐
                            │ alert session            │
                            │  ├─ sound thread  ───────┼──► compat/sound
                            │  ├─ banner flash  ───────┼──► compat/terminal
                            │  └─ key polling   ───────┼──► compat/keys
                            └──────────────────────────┘
```
Commands never talk to the daemon directly; both sides edit the store through
the same locked `store.update()` function. No sockets, no IPC, identical on
every OS.

### 5.2 Module Layout
```
tock/
├── __init__.py
├── __main__.py          # python -m tock
├── cli.py               # argparse subcommands, validation, output formatting
├── models.py            # Alarm dataclass, RepeatMode, next_occurrence(), status
├── timeparse.py         # "07:30", "07:30:15", "+10m", dates — locale-independent
├── store.py             # load / update(fn) / atomic write / corruption recovery
├── scheduler.py         # daemon loop, missed-alarm logic, snooze (clock injected)
├── alert.py             # alert session: sound thread + banner + key polling
├── config.py            # defaults (snooze, grace, ring timeout, tick)
└── compat/              # ── everything OS-specific lives here ──
    ├── __init__.py      # IS_WINDOWS / IS_MACOS / IS_LINUX / IS_WSL detection
    ├── paths.py         # per-OS data directory
    ├── locks.py         # exclusive file lock: fcntl (POSIX) / msvcrt (Windows)
    ├── sound.py         # backend chain + generated WAV
    ├── keys.py          # non-blocking line input: select (POSIX) / msvcrt (Windows)
    ├── terminal.py      # ANSI/VT enablement, colour + Unicode detection
    └── notify.py        # desktop notifications (Tier 3)
tests/
├── test_timeparse.py
├── test_models.py       # next_occurrence edge cases
├── test_store.py        # round-trip, corruption, atomic write, concurrent update
├── test_scheduler.py    # fake clock: fire, snooze, missed/grace, sleep-resume
├── test_cli.py          # parsing / validation / exit codes
└── test_compat.py       # backend selection with patched sys.platform / shutil.which; lock contention via subprocess
.github/workflows/ci.yml # matrix: ubuntu / macos / windows × Python 3.10 / 3.13
pyproject.toml           # console script: tock
README.md
```

### 5.3 Data Model
```python
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

class RepeatMode(str, Enum):
    ONCE = "once"
    DAILY = "daily"
    WEEKDAYS = "weekdays"
    WEEKLY = "weekly"

@dataclass
class Alarm:
    # required fields first (dataclass default-ordering rule)
    id: str                              # 8 hex chars from uuid4
    time: str                            # "HH:MM:SS"
    repeat: RepeatMode
    next_fire_at: datetime | None        # None once a one-off has fired
    # optional fields
    label: str = ""
    weekday: int | None = None           # 0=Mon … 6=Sun, WEEKLY only
    enabled: bool = True
    snoozed_until: datetime | None = None
    last_fired_at: datetime | None = None
    created_at: datetime = field(default_factory=datetime.now)

    def effective_fire_at(self) -> datetime | None:
        """snoozed_until takes precedence over the regular schedule."""
```

Derived status: `disabled` (not enabled) · `snoozed` (snoozed_until set) ·
`done` (ONCE, next_fire_at None, not snoozed) · otherwise `active`.

Store file:
```json
{
  "version": 1,
  "alarms": [
    {"id": "a1b2c3d4", "time": "07:30:00", "repeat": "daily",
     "next_fire_at": "2026-09-15T07:30:00", "label": "Wake up",
     "weekday": null, "enabled": true, "snoozed_until": null,
     "last_fired_at": null, "created_at": "2026-09-14T10:02:11"}
  ]
}
```
Datetimes are naive local ISO-8601 strings. An unknown `version` is refused
with a clear error rather than guessed at.

### 5.4 Core Algorithm: `next_occurrence(alarm, after: datetime) -> datetime`
- `DAILY` → today at `time` if `> after`, else tomorrow.
- `WEEKDAYS` → earliest Mon–Fri date at `time` that is `> after`.
- `WEEKLY` → earliest date with `weekday()` == `alarm.weekday` at `time` that is `> after`.
- `ONCE` → fixed at creation; after firing, `next_fire_at = None`.

Pure function, no I/O, no `now()` inside — fully unit-testable.

### 5.5 Scheduler Loop (daemon)
```
acquire daemon.lock (non-blocking) or exit 1: "daemon already running (pid N)"
install shutdown handlers (Ctrl+C everywhere; SIGTERM on POSIX; SIGBREAK on Windows)
last_tick = clock()
every TICK (0.5 s):
    now = clock()
    resumed = (now - last_tick) > 5 s          # sleep/resume or clock jump
    state = store.reload_if_changed()
    for alarm in due(state, now), oldest first:
        late_by = now - alarm.effective_fire_at()
        if (startup or resumed) and late_by > grace:
            store.update(mark_missed(alarm.id, now))   # advance, no ring
            print "Missed 07:30 'Wake up' (by 42 min)"
            continue
        store.update(mark_fired(alarm.id, now))        # commit BEFORE ringing
        outcome = alert.run(alarm)                     # STOP | SNOOZE | TIMEOUT
        if outcome is SNOOZE:
            store.update(set_snooze(alarm.id, clock() + snooze))
    last_tick = now
```
- **Commit before ringing:** if the daemon crashes mid-alert, the alarm is not
  re-fired in a loop on restart.
- **`mark_fired` / `mark_missed`:** set `last_fired_at`, clear
  `snoozed_until`; recurring → `next_fire_at = next_occurrence(after=now)`,
  so any number of missed occurrences collapse into one; one-off → `None`.
- **Alarms due during an alert** queue and ring straight after the current one
  is dismissed (grace applies only at startup / after resume, so they're never
  skipped just because another alarm was ringing).
- **`store.update(fn)`** = acquire `alarms.lock` → load fresh from disk →
  apply `fn` → atomic write → release. The daemon never writes a stale cached
  copy, which prevents lost updates from concurrent CLI commands.

### 5.6 Cross-Platform Layer (`compat/`)

| Concern | Windows | macOS | Linux | Fallback |
|---|---|---|---|---|
| **Data dir** (`paths.py`) | `%APPDATA%\tock` | `~/Library/Application Support/tock` | `$XDG_DATA_HOME/tock` or `~/.local/share/tock` | `TOCK_HOME` env var overrides all (used by tests) |
| **File lock** (`locks.py`) | `msvcrt.locking(fd, LK_NBLCK, 1)` | `fcntl.flock(LOCK_EX \| LOCK_NB)` | same as macOS | — (both stdlib) |
| **Atomic write** | `os.replace`, retried up to 5× on `PermissionError` (AV scanners / open handles) | `os.replace` | `os.replace` | temp file in same dir + `flush` + `fsync` |
| **Sound** (`sound.py`) | `winsound.PlaySound(wav, SND_FILENAME\|SND_ASYNC\|SND_LOOP)`, stop with `PlaySound(None, 0)` | `afplay wav` subprocess | first of `paplay` → `pw-play` → `aplay` on `PATH` | terminal bell `\a` once per second |
| **Keys** (`keys.py`) | poll `msvcrt.kbhit()` / `getwch()`, buffer until `\r` | `select.select([stdin], [], [], 0.2)` + `readline()` | same as macOS | no TTY → no prompt, auto-stop after ring timeout |
| **Colour** (`terminal.py`) | enable VT mode via `ctypes` `SetConsoleMode(… \| 0x0004)` | ANSI | ANSI | plain text if not a TTY, `NO_COLOR` set, `TERM=dumb`, or VT enable fails |
| **Unicode** (`terminal.py`) | test-encode `⏰` against `sys.stdout.encoding` | same | same | ASCII `[ALARM]` |
| **Shutdown** | `KeyboardInterrupt`, `SIGBREAK` | `KeyboardInterrupt`, `SIGTERM` | same as macOS | always stop sound + release locks in `finally` |
| **WSL** (Tier 3) | — | — | detected via `/proc/version`; play WAV through `powershell.exe` `Media.SoundPlayer` with `wslpath -w` | bell |
| **Desktop notify** (Tier 3) | PowerShell toast | `osascript -e 'display notification …'` | `notify-send` | skipped silently |

**Key decisions**
- **Generated alarm sound.** On first use, write a short 880 Hz beep pattern to
  `<data dir>/alarm.wav` using stdlib `wave` + `math` + `struct`. WAV is the
  one format `winsound`, `afplay` and `aplay` all play natively — so every OS
  gets a real alarm tone with no bundled binary asset. `--sound PATH` overrides
  it with a user WAV.
- **Single-instance via lock, not pidfile.** An OS file lock is released
  automatically when the process dies, so there are no stale pidfiles to clean
  up. (Also avoids a real trap: on Windows `os.kill(pid, 0)` does not "probe" a
  process — it terminates it.) The PID is written into the lock file for
  display only.
- **No ANSI blink code.** `\e[5m` is ignored by most terminals (including
  Windows Terminal); the banner "flashes" by redrawing normal ↔ inverse every
  0.5 s with `\r`, which works everywhere VT is enabled.
- **Line-based input on all OSes** (`Enter` / `s`+`Enter`) rather than raw
  single keypresses — avoids `termios` raw mode, so the terminal can never be
  left in a broken state after a crash.
- **Sound runs in a daemon thread** controlled by a `threading.Event`; stopping
  terminates any child player process. The main thread owns banner + input,
  so the alert is always dismissable.

### 5.7 CLI Surface
```
tock add 07:30 --label "Wake up" --repeat daily
tock add 07:30 --repeat weekly --day mon
tock add 22:00 2026-09-20 --label "Wind down"
tock add +10m --label "Tea"
tock list
tock toggle a1b2
tock remove a1b2
tock clear
tock daemon [--snooze 5] [--grace 15] [--ring-timeout 300] [--sound PATH] [--notify] [--no-color]
tock doctor [--sound]
```
Exit codes: `0` success · `1` runtime error (e.g. daemon already running) ·
`2` invalid usage/input (argparse convention). Errors go to stderr as one
friendly line, never a traceback (full traceback with `--debug`).

Invocation on every OS: `python -m tock …` (or `py -m tock …` on
Windows), or `tock …` after `pip install .`.

## 6. Implementation Plan (tiered)

The app is complete and submittable at the end of every tier. Anything not
reached goes into the README's *Future work* section with a one-line reason.

### Tier 1 — Core, cross-platform from day one (~5 h)
| Step | Deliverable | Effort |
|---|---|---|
| 1.1 | `timeparse.py` + `models.py` (ONCE/DAILY) + tests | 1 h |
| 1.2 | `compat/paths.py`, `compat/locks.py`; `store.py` with locked `update()`, atomic write, corruption backup + tests | 1 h |
| 1.3 | `compat/terminal.py`, `compat/sound.py` (generated WAV, per-OS backends, bell fallback) | 1 h |
| 1.4 | `scheduler.py` (single-instance lock, tick loop, fire, commit-before-ring) + `alert.py` (Stop + ring timeout) + fake-clock tests | 1 h |
| 1.5 | `cli.py`: `add` (absolute + `+Nm`), `list`, `remove`, `daemon`, `doctor` | 0.5 h |
| 1.6 | GitHub Actions matrix (3 OS × 2 Python) running unit tests + CLI smoke test; README quick-start | 0.5 h |

### Tier 2 — Complete alarm clock (~3.5 h)
| Step | Deliverable | Effort |
|---|---|---|
| 2.1 | `compat/keys.py` + snooze (persisted) | 1 h |
| 2.2 | Missed alarms: grace window, sleep/resume detection, coalescing + tests | 1 h |
| 2.3 | WEEKDAYS / WEEKLY + `--day`, combination validation + tests | 0.5 h |
| 2.4 | `toggle`, `clear`, countdowns and status in `list` | 0.5 h |
| 2.5 | Non-TTY mode; manual test pass on Windows + Linux/WSL (+ macOS via CI) | 0.5 h |

### Tier 3 — Polish (~2–3 h, pick in order)
1. README: design decisions, trade-offs, per-OS notes, asciinema/GIF demo
2. WSL audio via `powershell.exe`
3. Desktop notifications (`--notify`)
4. Background-run guide: Windows Task Scheduler, macOS `launchd` plist, Linux `systemd --user` unit
5. 12-hour input (`7:30am`)

## 7. Test Plan

**Automated (stdlib `unittest`, runs in CI on Windows, macOS, Linux)**
- `timeparse`: valid/invalid formats (`25:00`, `7:5`, `+0m`, `+99h`), past dates rejected.
- `next_occurrence`: midnight rollover, exact-now boundary (`> after`, not `>=`),
  Friday → Monday for WEEKDAYS, same-day-later vs next-week for WEEKLY,
  month/year/leap-day rollover.
- `store`: round-trip, unknown version refused, corrupt JSON → backup + empty,
  interrupted write leaves the original intact, two processes calling
  `update()` concurrently lose no data (real subprocesses, exercises the
  per-OS lock).
- `scheduler` (fake clock, fake alert): fires on time; commit-before-ring;
  snooze re-fires once after N min; recurring advances; startup with alarm
  missed by 5 min → fires, by 2 h → skipped; week-long gap → recurring fires at
  most once; alarm due while another is ringing → rings next; store edited
  mid-loop → picked up; second daemon refused.
- `compat`: backend selection under patched `sys.platform` / `shutil.which`
  (e.g. Linux with no players → bell); `NO_COLOR` / non-TTY → plain; ASCII
  fallback for a cp1252 stdout; `TOCK_HOME` override.
- `cli`: argument errors exit 2 with a message; `remove` with ambiguous prefix.

**CI smoke test (every OS):** `add +1m`, `list`, `doctor`, `remove`, `clear`
against a temp `TOCK_HOME`.

**Manual checklist (things CI can't hear or see)**

| Environment | Sound | Banner/colour | Enter / Snooze | Ctrl+C clean exit |
|---|---|---|---|---|
| Windows Terminal (PowerShell) | | | | |
| Windows `cmd.exe` | | | | |
| macOS Terminal / iTerm2 | | | | |
| Linux (GNOME Terminal etc.) | | | | |
| WSL | | | | |
| SSH session / no audio device | bell | | | |
| Output piped to file (non-TTY) | | plain | auto-stop | |

## 8. Acceptance Criteria
1. The same commands produce the same behaviour on Windows, macOS and Linux;
   the CI matrix is green on all three.
2. `python -m tock` and `python -m unittest` work on a stock Python 3.10+
   install with zero `pip install`.
3. `add` rejects malformed and past times with a clear one-line message (exit 2).
4. The daemon fires each alarm within ~1 s of schedule, with audible sound on a
   machine that has audio, and a terminal bell otherwise.
5. Snooze re-fires exactly N minutes later, once, and survives a daemon restart.
6. Restarting the daemon preserves all alarms; a missed alarm follows the grace rule.
7. Adding an alarm from a second terminal while the daemon runs is never lost
   and is picked up within ~1 s.
8. Starting a second daemon exits with a clear message.
9. `doctor` accurately reports the sound backend and terminal capabilities.

## 9. Limitations / Known Trade-offs
- **Sleeping machine:** a CLI process cannot wake the computer; alarms fire (or
  are reported missed) when it resumes. Documented prominently.
- **Daemon must be running** in a terminal; background-service setup is a
  documented manual step per OS.
- **DST:** naive local time. An alarm inside a spring-forward gap fires when the
  clock jumps past it; a fall-back overlap fires once (next occurrence is
  computed immediately after firing).
- **Git Bash / mintty on Windows** doesn't expose a real console, so keypresses
  aren't detected → runs in non-TTY mode. Workaround: use PowerShell / Windows
  Terminal, or `winpty python -m tock daemon`.
- **Terminal bell** may be muted by OS or terminal settings; `doctor --sound`
  lets the user verify audio before relying on it.
- **WSL audio** needs WSLg/PulseAudio or the Tier 3 PowerShell bridge;
  otherwise falls back to the bell.
