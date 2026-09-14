"""Foreground visual and audible alert sessions."""

from __future__ import annotations

import sys
import time
from enum import Enum
from pathlib import Path

from .compat.keys import LinePoller
from .compat.notify import DesktopNotifier
from .compat.sound import SoundPlayer, generate_alarm_wav
from .compat.terminal import alarm_banner
from .control import ControlChannel
from .models import Alarm


class AlertOutcome(str, Enum):
    STOP = "stop"
    TIMEOUT = "timeout"
    SNOOZE = "snooze"
    SHUTDOWN = "shutdown"


REQUEST_OUTCOMES = {"dismiss": AlertOutcome.STOP, "snooze": AlertOutcome.SNOOZE, "stop": AlertOutcome.SHUTDOWN}


def disabled_notifier(*, timeout: float) -> DesktopNotifier:
    return DesktopNotifier(timeout=timeout, backend="none")


def display_time(alarm_time: str) -> str:
    return alarm_time[:5] if alarm_time.endswith(":00") else alarm_time


class AlertSession:
    def __init__(self, data_directory: Path, *, ring_timeout: float = 300, stream=None, clock=time.monotonic, sleep=time.sleep,
                 control: ControlChannel | None = None, poller: LinePoller | None = None, player_factory=SoundPlayer,
                 notifier_factory=DesktopNotifier) -> None:
        self.data_directory = data_directory
        self.ring_timeout = ring_timeout
        self.stream = stream or sys.stdout
        self.clock = clock
        self.sleep = sleep
        self.control = control or ControlChannel(data_directory)
        self.poller = poller
        self.player_factory = player_factory
        self.notifier_factory = notifier_factory

    def run(self, alarm: Alarm) -> AlertOutcome:
        """Ring until Enter, a popup button, a dismiss/snooze/stop request, or timeout."""
        sound = self.player_factory(generate_alarm_wav(self.data_directory / "alarm.wav"), stream=self.stream)
        notifier = self.notifier_factory(timeout=self.ring_timeout)
        poller = self.poller or LinePoller()
        started, inverse = self.clock(), False
        requests_since = self.control.clock()
        # A background daemon writes to a log, so draw the banner once instead of flashing it.
        redraw = self.stream.isatty()
        self.control.mark_ringing(alarm)
        sound.start()
        notifier.show(alarm.label or "Alarm", f"Alarm at {display_time(alarm.time)}")
        try:
            if notifier.error:
                print(notifier.error, file=self.stream, flush=True)
            if poller.available:
                print("Press Enter to stop.", file=self.stream, flush=True)
            else:
                popup = "Click Stop or Snooze in the popup, or run" if getattr(notifier, "interactive", False) else "Run"
                print(f"{popup} 'tock dismiss' to stop or 'tock snooze' to snooze.", file=self.stream, flush=True)
            first = True
            while self.clock() - started < self.ring_timeout:
                if redraw or first:
                    print(alarm_banner(alarm.time, alarm.label, inverse=inverse, stream=self.stream), file=self.stream, flush=True)
                    inverse, first = not inverse, False
                response = poller.poll()
                if response is not None:
                    return AlertOutcome.SNOOZE if response.strip().lower() == "s" else AlertOutcome.STOP
                clicked = notifier.poll()
                if clicked is not None:
                    return REQUEST_OUTCOMES[clicked]
                request = self.control.receive(since=requests_since)
                if request is not None:
                    return REQUEST_OUTCOMES[request]
                self.sleep(0.5)
            return AlertOutcome.TIMEOUT
        finally:
            notifier.close()
            sound.stop()
            self.control.clear_ringing()
