# Tock

A dependency-free, cross-platform alarm clock for the terminal. It uses a
daemon process and a JSON file rather than an OS service or database, so it
works the same way on Windows, macOS, Linux, and WSL with Python 3.10+.

## Quick start

```bash
python -m tock start                    # start the background daemon
python -m tock add +1m --label "Tea"    # ring in one minute
python -m tock stop                     # stop the daemon when you're done
```

Run the commands from this directory. Use `py -m tock` on Windows if `python`
isn't on your PATH, or run `pip install .` and use `tock` directly.

> **Alarms only ring while the daemon is running.** `add`, `list`, `toggle`,
> and `remove` just edit the saved alarm file. If the daemon was off when an
> alarm was due, it rings on the next start if it's no more than `--grace`
> minutes late (default 15); otherwise it's marked missed.

## Examples

**Add alarms**

```bash
python -m tock add +10m --label "Tea"                      # in 10 minutes
python -m tock add 07:30 --label "Wake up" --repeat daily  # every day
python -m tock add 09:00 --repeat weekdays                 # Monday to Friday
python -m tock add 18:00 --repeat weekly --day fri         # every Friday
python -m tock add 22:00 2026-09-20 --label "Wind down"    # on a specific date
```

**Manage alarms**

```bash
python -m tock list            # show alarms and their IDs
python -m tock toggle a1b2     # pause or resume an alarm (any unique ID prefix)
python -m tock remove a1b2     # delete an alarm
python -m tock clear           # remove finished one-off alarms
```

**When an alarm rings**

A sound plays and a popup appears with **Stop** and **Snooze** buttons. You can
also respond from any terminal:

```bash
python -m tock dismiss         # stop it
python -m tock snooze          # ring again in 5 minutes
python -m tock status          # is the daemon running? is anything ringing?
```

If nobody responds, the alarm stops after 5 minutes.

**Change the defaults**

```bash
python -m tock start --snooze 10 --ring-timeout 60    # 10-minute snooze, stop ringing after 60s
python -m tock start --no-notify                      # sound only, no popup
```

**Check your setup**

```bash
python -m tock doctor --notify --sound    # test the popup and sound on this machine
```

### Foreground mode

To watch alarms in a terminal instead, run `python -m tock daemon` and keep it
open. Press Enter to stop a ringing alarm, `s` then Enter to snooze, and Ctrl+C
to quit. `daemon` accepts the same options as `start`. Only one daemon can run
per user, in either mode.

## Platform support

| Platform | Popup | Sound |
| --- | --- | --- |
| Windows | PowerShell window with Stop/Snooze | `winsound` |
| WSL | Windows popup through `powershell.exe` | `paplay`/`aplay` if installed, else Windows through `powershell.exe` |
| macOS | `osascript` dialog with Stop/Snooze | `afplay` |
| Linux | `zenity` dialog with Stop/Snooze, else a `notify-send` notification | `paplay`, `pw-play`, or `aplay` |

Limits:

- The background daemon runs until `stop` or a restart (logging out may also
  end it). To start it at login, run `python -m tock start` from Task Scheduler
  (Windows), a launchd agent (macOS), or your shell profile or a systemd user
  unit (Linux).
- On WSL, the daemon ends when the WSL VM shuts down.
- With no popup tool, alarms still ring and are logged. With no audio player,
  Tock falls back to the terminal bell, which you won't hear from a
  background daemon.
- A user-space CLI can't wake a sleeping machine; see `--grace` above.

The daemon logs to `daemon.log` in the data directory; `status` prints its path.

## Design notes

- Alarm state lives in platform-specific app data: `%APPDATA%\tock` on
  Windows, `~/Library/Application Support/tock` on macOS, and
  `$XDG_DATA_HOME/tock` (or `~/.local/share/tock`) on Linux. Set `TOCK_HOME`
  to override it, especially for tests.
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

## Development

No runtime or test packages need installation:

```bash
python -m unittest discover -v
```

The tests inject clock and alert behavior; they do not wait for real alarms or
play real audio.
