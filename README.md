# Tock

A dependency-free, cross-platform alarm clock for the terminal. It uses a
daemon process and a JSON file rather than an OS service or database, so it
works the same way on Windows, macOS, Linux, and WSL with Python 3.10+.

## Running the project

> **Important:** alarms only ring while the daemon is running. Commands such
> as `add`, `list`, `toggle`, and `remove` just edit the saved alarm file; they
> do not schedule anything themselves. If the daemon is not running when an
> alarm is due, nothing rings. When the daemon starts again, alarms that are
> overdue by no more than `--grace` minutes ring, and older ones are marked
> missed.

The daemon can run in the background (recommended) or in the foreground.
Run the commands from this directory. You can add alarms before or after
starting the daemon, because it reloads the alarm file about every half second.

### Option A: background daemon

```bash
python -m tock start                 # start the daemon; it keeps running after you close the terminal
python -m tock add +1m --label "Tea"
python -m tock status                # is it running? is an alarm ringing?
python -m tock dismiss               # stop the ringing alarm
python -m tock snooze                # or snooze it
python -m tock stop                  # stop the daemon
```

When an alarm rings, the sound plays and a popup appears on top of other
windows. It shows the alarm's label and time, with **Stop** and **Snooze**
buttons. A background daemon has no keyboard, so use the popup or run
`dismiss`/`snooze` from any terminal. If nobody responds, the alarm stops after
`--ring-timeout` seconds (default 300). The daemon's output, including
missed-alarm notices, goes to `daemon.log` in the data directory. `status`
prints its path.

Check that popups and sound work on your machine before you rely on them:

```bash
python -m tock doctor --notify --sound
```

### Option B: foreground daemon (two terminals)

**Terminal 1:** run the daemon and keep this terminal open.

```bash
python -m tock daemon
```

Alarms ring here and also show the popup. Press Enter to stop one, type `s`
then Enter to snooze it, or use the popup buttons. Press Ctrl+C to stop the
daemon.

**Terminal 2:** manage alarms.

```bash
python -m tock add +1m --label "Tea"
python -m tock list
```

Only one daemon can run per user, in either mode. A second one exits with
`error: daemon already running`.

### Limits

- The background daemon lasts until you run `stop` or restart the machine
  (logging out may also end it, depending on the system). To
  start it automatically at login, run `python -m tock start` from
  Task Scheduler (Windows), a launchd agent (macOS), or your shell profile or
  a systemd user unit (Linux).
- On WSL, the daemon ends when the WSL VM shuts down, for example after
  `wsl --shutdown` or when no WSL terminal is left open.
- If a machine has no popup tool, see the table below: alarms still ring and
  are logged, but nothing appears on screen. Pass `--no-notify` to `start` or
  `daemon` to turn popups off.
- A machine with no audio player falls back to the terminal bell, which you
  won't hear from a background daemon.

| Platform | Popup | Sound |
| --- | --- | --- |
| Windows | PowerShell window with Stop/Snooze | `winsound` |
| WSL | Windows popup through `powershell.exe` | `paplay`/`aplay` if installed, else Windows through `powershell.exe` |
| macOS | `osascript` dialog with Stop/Snooze | `afplay` |
| Linux | `zenity` dialog with Stop/Snooze, else a `notify-send` notification | `paplay`, `pw-play`, or `aplay` |

Use `py -m tock` on Windows where `python` is not available. `python -m
tock doctor` shows the selected local audio fallback and terminal
capabilities.

## Commands

```text
python -m tock add 07:30 --label "Wake up" --repeat daily
python -m tock add 07:30 --repeat weekdays
python -m tock add 07:30 --repeat weekly --day mon
python -m tock add 22:00 2026-09-20 --label "Wind down"
python -m tock add +10m --label "Tea"
python -m tock list
python -m tock toggle <id-prefix>
python -m tock remove <id-prefix>
python -m tock clear
python -m tock start --snooze 5 --grace 15 --ring-timeout 300
python -m tock status
python -m tock dismiss
python -m tock snooze
python -m tock stop
python -m tock daemon --snooze 5 --grace 15 --ring-timeout 300
python -m tock start --no-notify
python -m tock doctor --notify --sound
```

`start` and `daemon` accept the same options. The popup buttons, `dismiss`,
and `snooze` work in both modes. In a foreground daemon you can also press Enter
to stop an alert or type `s` then Enter to snooze it.

## Design notes

- Alarm state lives in platform-specific app data: `%APPDATA%\\tock` on
  Windows, `~/Library/Application Support/tock` on macOS, and
  `$XDG_DATA_HOME/tock` (or `~/.local/share/tock`) on Linux. Set
  `TOCK_HOME` to override it, especially for tests.
- Updates use an advisory lock and atomic `os.replace`, preventing CLI edits
  from clobbering daemon state. A malformed JSON store is retained with a
  `.corrupt-<timestamp>` suffix and the app begins empty.
- The daemon owns a separate lock, so a second daemon fails clearly rather
  than firing the same alarm twice.
- `start` launches `python -m tock daemon` as a detached process (a new
  session on POSIX; no console window on Windows) and waits until it writes
  `daemon.pid`. CLI commands never talk to the daemon directly. `stop`,
  `dismiss`, and `snooze` drop a timestamped `request.json` that the daemon
  consumes on its next tick, and the daemon publishes the ringing alarm in
  `ringing.json`. If a stop request goes unanswered, `stop` falls back to
  SIGTERM.
- Sound is a generated WAV played through `winsound`, `afplay`, `paplay`,
  `pw-play`, `aplay`, or Windows' `SoundPlayer` from WSL; a terminal bell is the
  universal fallback.
- Popups run as a child process (PowerShell WinForms, `osascript`, or `zenity`)
  so the daemon keeps ticking. The daemon reads the clicked button from the
  child's output and closes the popup when the alert ends another way.
  PowerShell scripts use `-EncodedCommand`, and labels are passed as quoted
  literals or plain arguments, so a label can't inject commands.
- A user-space CLI cannot wake a sleeping machine. On resume, overdue alarms
  within the configured grace window ring; older alarms are recorded as missed.

## Development

No runtime or test packages need installation:

```bash
python -m unittest discover -v
```

The tests inject clock and alert behavior; they do not wait for real alarms or
play real audio.
