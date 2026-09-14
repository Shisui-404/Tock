"""Command-line interface for managing persisted alarms."""

from __future__ import annotations

import argparse
import os
import platform
import signal
import sys
import time
import uuid
from datetime import datetime, timedelta

from .compat.notify import DesktopNotifier, selected_notifier
from .compat.paths import data_directory
from .compat.sound import SoundPlayer, generate_alarm_wav, selected_backend
from .compat.terminal import supports_colour, supports_unicode
from .control import ControlChannel
from .lifecycle import DaemonLifecycleError, start_background, stop_daemon
from .models import Alarm, RepeatMode
from .scheduler import DaemonAlreadyRunningError, Scheduler
from .store import AlarmStore, StoreError
from .timeparse import TimeParseError, parse_absolute, parse_relative


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tock", description="A dependency-free command-line alarm clock.")
    parser.add_argument("--debug", action="store_true", help="show tracebacks for runtime errors")
    commands = parser.add_subparsers(dest="command", required=True)

    add = commands.add_parser("add", help="create an alarm")
    add.add_argument("when", help="HH:MM[:SS] or +Ns/+Nm/+Nh")
    add.add_argument("date", nargs="?", help="optional YYYY-MM-DD for an absolute time")
    add.add_argument("--label", default="", help="message displayed when the alarm rings")
    add.add_argument("--repeat", choices=[mode.value for mode in RepeatMode], default="once")
    add.add_argument("--day", choices=("mon", "tue", "wed", "thu", "fri", "sat", "sun"), help="weekday for --repeat weekly")

    commands.add_parser("list", help="list alarms")
    remove = commands.add_parser("remove", help="remove an alarm by unique ID prefix")
    remove.add_argument("id")
    toggle = commands.add_parser("toggle", help="enable or disable an alarm by unique ID prefix")
    toggle.add_argument("id")
    commands.add_parser("clear", help="purge completed one-off alarms")
    _add_daemon_options(commands.add_parser("daemon", help="run the alarm scheduler in this terminal"))
    _add_daemon_options(commands.add_parser("start", help="run the alarm scheduler in the background"))
    commands.add_parser("stop", help="stop the running daemon")
    commands.add_parser("status", help="show whether the daemon is running")
    commands.add_parser("dismiss", help="stop the alarm that is ringing")
    commands.add_parser("snooze", help="snooze the alarm that is ringing")

    doctor = commands.add_parser("doctor", help="report local capabilities")
    doctor.add_argument("--sound", action="store_true", help="play a two-second sound test")
    doctor.add_argument("--notify", action="store_true", help="show a test alarm popup")
    return parser


def _add_daemon_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--ring-timeout", type=float, default=300, metavar="SECONDS", help="auto-stop an alert after this many seconds")
    parser.add_argument("--snooze", type=float, default=5, metavar="MINUTES", help="snooze duration")
    parser.add_argument("--grace", type=float, default=15, metavar="MINUTES", help="ring missed alarms within this window")
    parser.add_argument("--no-notify", dest="notify", action="store_false", help="do not show a desktop popup when an alarm rings")


def _validate_daemon_options(args: argparse.Namespace) -> None:
    if args.ring_timeout <= 0:
        raise ValueError("--ring-timeout must be greater than zero")
    if args.snooze <= 0 or args.grace < 0:
        raise ValueError("--snooze must be greater than zero and --grace cannot be negative")


def _find_unique(alarms: list[Alarm], prefix: str) -> Alarm:
    matches = [alarm for alarm in alarms if alarm.id.startswith(prefix.lower())]
    if not matches:
        raise ValueError(f"no alarm matches '{prefix}'")
    if len(matches) > 1:
        raise ValueError(f"alarm ID prefix '{prefix}' is ambiguous")
    return matches[0]


def command_add(args: argparse.Namespace, store: AlarmStore, now: datetime) -> str:
    if args.when.startswith("+"):
        if args.date:
            raise TimeParseError("a relative alarm cannot have a date")
        if args.repeat != RepeatMode.ONCE.value:
            raise TimeParseError("a relative alarm can only repeat once")
        fire_at = parse_relative(args.when, now)
    else:
        if args.date and args.repeat != RepeatMode.ONCE.value:
            raise TimeParseError("an explicit date cannot be combined with a repeating alarm")
        fire_at = parse_absolute(args.when, args.date, now)

    repeat = RepeatMode(args.repeat)
    weekdays = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
    if args.day and repeat is not RepeatMode.WEEKLY:
        raise TimeParseError("--day can only be used with --repeat weekly")
    weekday = weekdays.index(args.day) if args.day else (fire_at.weekday() if repeat is RepeatMode.WEEKLY else None)
    alarm = Alarm(id=uuid.uuid4().hex[:8], time=fire_at.strftime("%H:%M:%S"), repeat=repeat,
                  next_fire_at=fire_at, label=args.label, weekday=weekday)
    store.update(lambda alarms: alarms.append(alarm))
    return f"Added {alarm.id}: {fire_at:%Y-%m-%d %H:%M:%S}" + (f" — {alarm.label}" if alarm.label else "")


def command_list(store: AlarmStore, now: datetime) -> str:
    alarms = sorted(store.load(), key=lambda alarm: (alarm.effective_fire_at() is None, alarm.effective_fire_at() or datetime.max))
    if not alarms:
        return "No alarms. Add one with: tock add +10m --label \"Tea\""
    rows = ["ID        NEXT FIRE             REPEAT  STATUS    LABEL"]
    for alarm in alarms:
        fire_at = alarm.effective_fire_at()
        next_text = fire_at.strftime("%Y-%m-%d %H:%M:%S") if fire_at else "—"
        countdown = "" if fire_at is None else f" ({max(fire_at - now, timedelta())})"
        rows.append(f"{alarm.id:<8}  {next_text:<20}  {alarm.repeat.value:<8}  {alarm.status:<8}  {alarm.label}{countdown}")
    return "\n".join(rows)


def command_remove(args: argparse.Namespace, store: AlarmStore) -> str:
    removed: Alarm | None = None

    def operation(alarms: list[Alarm]) -> None:
        nonlocal removed
        removed = _find_unique(alarms, args.id)
        alarms.remove(removed)

    store.update(operation)
    return f"Removed {removed.id}"  # type: ignore[union-attr]


def command_toggle(args: argparse.Namespace, store: AlarmStore, now: datetime) -> str:
    changed: Alarm | None = None

    def operation(alarms: list[Alarm]) -> None:
        nonlocal changed
        changed = _find_unique(alarms, args.id)
        changed.enabled = not changed.enabled
        if changed.enabled and changed.next_fire_at is None:
            candidate = datetime.combine(now.date(), changed.scheduled_time())
            changed.next_fire_at = candidate if candidate > now else candidate + timedelta(days=1)

    store.update(operation)
    return f"{'Enabled' if changed.enabled else 'Disabled'} {changed.id}"  # type: ignore[union-attr]


def command_clear(store: AlarmStore) -> str:
    removed = 0

    def operation(alarms: list[Alarm]) -> None:
        nonlocal removed
        before = len(alarms)
        alarms[:] = [alarm for alarm in alarms if alarm.status != "done"]
        removed = before - len(alarms)

    store.update(operation)
    return f"Cleared {removed} completed alarm(s)."


def command_daemon(args: argparse.Namespace, store: AlarmStore) -> str:
    _validate_daemon_options(args)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _interrupt)  # let a forced stop still silence sound and clean up
    print("Alarm daemon running. Press Ctrl+C or run 'tock stop' to stop.")
    Scheduler(store, ring_timeout=args.ring_timeout, snooze_minutes=args.snooze, grace_minutes=args.grace,
              notify=args.notify).run()
    return "Alarm daemon stopped."


def _interrupt(signum, frame) -> None:
    raise KeyboardInterrupt


def command_start(args: argparse.Namespace, store: AlarmStore) -> str:
    _validate_daemon_options(args)
    daemon_args = ["--ring-timeout", str(args.ring_timeout), "--snooze", str(args.snooze), "--grace", str(args.grace)]
    if not args.notify:
        daemon_args.append("--no-notify")
    pid = start_background(store.directory, daemon_args)
    log_path = ControlChannel(store.directory).log_path
    return f"Alarm daemon started in the background (pid {pid}).\nLog: {log_path}\nStop it with: tock stop"


def command_stop(store: AlarmStore) -> str:
    pid = stop_daemon(store.directory)
    return f"Alarm daemon stopped (pid {pid})." if pid else "Alarm daemon stopped."


def command_status(store: AlarmStore) -> str:
    control = ControlChannel(store.directory)
    if not control.daemon_running():
        return "Daemon: not running. Start it with: tock start"
    pid = control.daemon_pid()
    lines = [f"Daemon: running (pid {pid})" if pid else "Daemon: running", f"Log: {control.log_path}"]
    ringing = control.ringing()
    if ringing:
        lines.append(f"Ringing: {_describe(ringing)}")
    return "\n".join(lines)


def command_respond(store: AlarmStore, action: str, *, timeout: float = 3) -> str:
    """Dismiss or snooze the ringing alarm, waiting for the daemon to acknowledge."""
    control = ControlChannel(store.directory)
    if not control.daemon_running():
        raise DaemonLifecycleError("daemon is not running")
    ringing = control.ringing()
    if ringing is None:
        return "No alarm is ringing."
    control.send(action)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = control.ringing()
        if current is None or current.get("id") != ringing.get("id"):
            return f"{'Dismissed' if action == 'dismiss' else 'Snoozed'} {_describe(ringing)}"
        time.sleep(0.1)
    raise DaemonLifecycleError("daemon did not respond; check 'tock status'")


def _describe(ringing: dict) -> str:
    label = ringing.get("label")
    return f"{ringing.get('id')} {ringing.get('time')}" + (f" — {label}" if label else "")


def command_doctor(*, play_sound: bool = False, show_popup: bool = False) -> str:
    directory = data_directory()
    control = ControlChannel(directory)
    daemon = "not running"
    if control.daemon_running():
        pid = control.daemon_pid()
        daemon = f"running (pid {pid})" if pid else "running"
    popup = _test_popup() if show_popup else ""
    if play_sound:
        player = SoundPlayer(generate_alarm_wav(directory / "alarm.wav"))
        player.start()
        try:
            time.sleep(2)
        finally:
            player.stop()
    return "\n".join((
        f"OS: {platform.platform()}",
        f"Python: {platform.python_version()}",
        f"Data directory: {directory}",
        f"Daemon: {daemon}",
        f"Sound backend: {selected_backend()}",
        f"Notification backend: {selected_notifier()}",
        f"TTY output: {'yes' if sys.stdout.isatty() else 'no'}",
        f"Colour enabled: {'yes' if supports_colour() else 'no'}",
        f"Unicode output: {'yes' if supports_unicode() else 'no'}",
    ) + ((f"Popup test: {popup}",) if show_popup else ()))


def _test_popup(timeout: float = 30) -> str:
    notifier = DesktopNotifier(timeout=timeout)
    notifier.show("Test alarm", "Notifications from tock work. Click a button.")
    if notifier.error or not notifier.active:
        return notifier.error or "no notification backend available"
    if not notifier.interactive:
        return f"notification sent with {notifier.backend}"
    deadline = time.monotonic() + timeout + 5
    try:
        while time.monotonic() < deadline:
            clicked = notifier.poll()
            if clicked is not None:
                return f"clicked {'Stop' if clicked == 'dismiss' else 'Snooze'}"
            if not notifier.active:
                return "closed without a click"
            time.sleep(0.1)
        return "timed out"
    finally:
        notifier.close()


def run(args: argparse.Namespace, *, store: AlarmStore | None = None, now: datetime | None = None) -> str:
    store = store or AlarmStore()
    now = now or datetime.now()
    if args.command == "add":
        return command_add(args, store, now)
    if args.command == "list":
        return command_list(store, now)
    if args.command == "remove":
        return command_remove(args, store)
    if args.command == "toggle":
        return command_toggle(args, store, now)
    if args.command == "clear":
        return command_clear(store)
    if args.command == "daemon":
        return command_daemon(args, store)
    if args.command == "start":
        return command_start(args, store)
    if args.command == "stop":
        return command_stop(store)
    if args.command == "status":
        return command_status(store)
    if args.command in ("dismiss", "snooze"):
        return command_respond(store, args.command)
    if args.command == "doctor":
        return command_doctor(play_sound=args.sound, show_popup=args.notify)
    raise ValueError(f"unknown command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        print(run(args))
        return 0
    except (TimeParseError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except (StoreError, DaemonAlreadyRunningError, DaemonLifecycleError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nAlarm daemon stopped.")
        return 0
    except Exception as error:
        if args.debug:
            raise
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
