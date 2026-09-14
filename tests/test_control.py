from datetime import datetime
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import os
import subprocess
import sys
import unittest

from tock.alert import AlertOutcome, AlertSession, disabled_notifier
from tock.cli import build_parser, run
from tock.compat.locks import FileLock
from tock.compat.process import process_alive
from tock.control import ControlChannel
from tock.lifecycle import DaemonLifecycleError, stop_daemon
from tock.models import Alarm, RepeatMode
from tock.scheduler import DaemonAlreadyRunningError, Scheduler
from tock.store import AlarmStore


class SilentPlayer:
    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        pass

    def stop(self):
        pass


class NoKeyboard:
    available = False

    def poll(self):
        return None


class ControlChannelTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.now = 1_000.0
        self.control = ControlChannel(self.directory, clock=lambda: self.now)

    def tearDown(self):
        self.temp.cleanup()

    def test_request_is_consumed_once(self):
        self.control.send("dismiss")
        self.assertEqual(self.control.receive(since=0), "dismiss")
        self.assertIsNone(self.control.receive(since=0))

    def test_request_sent_before_since_is_discarded(self):
        self.control.send("snooze")
        self.assertIsNone(self.control.receive(since=self.now + 1))
        self.assertFalse(self.control.request_path.exists())

    def test_daemon_running_reflects_lock(self):
        self.assertFalse(self.control.daemon_running())
        with FileLock(self.control.lock_path, blocking=False):
            self.assertTrue(self.control.daemon_running())


class AlertRequestTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.control = ControlChannel(self.directory)
        self.alarm = Alarm("abcdefgh", "07:30:00", RepeatMode.ONCE, datetime(2026, 9, 14, 7, 30), label="Tea")
        self.stream = StringIO()

    def tearDown(self):
        self.temp.cleanup()

    def session(self, on_sleep):
        return AlertSession(self.directory, ring_timeout=60, stream=self.stream, sleep=on_sleep, control=self.control,
                            poller=NoKeyboard(), player_factory=SilentPlayer, notifier_factory=disabled_notifier)

    def assert_request_outcome(self, action, outcome):
        seen = []

        def on_sleep(_):
            seen.append(self.control.ringing())
            self.control.send(action)

        self.assertIs(self.session(on_sleep).run(self.alarm), outcome)
        self.assertEqual(seen[0]["id"], "abcdefgh")
        self.assertIsNone(self.control.ringing())

    def test_dismiss_request_stops_alert(self):
        self.assert_request_outcome("dismiss", AlertOutcome.STOP)

    def test_snooze_request_snoozes_alert(self):
        self.assert_request_outcome("snooze", AlertOutcome.SNOOZE)

    def test_stop_request_shuts_down(self):
        self.assert_request_outcome("stop", AlertOutcome.SHUTDOWN)

    def test_banner_is_not_repeated_without_tty(self):
        ticks = iter(range(0, 10))
        session = AlertSession(self.directory, ring_timeout=3, stream=self.stream, clock=lambda: next(ticks),
                               sleep=lambda _: None, control=self.control, poller=NoKeyboard(), player_factory=SilentPlayer,
                               notifier_factory=disabled_notifier)
        self.assertIs(session.run(self.alarm), AlertOutcome.TIMEOUT)
        self.assertEqual(self.stream.getvalue().count("07:30:00"), 1)


class SchedulerControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.store = AlarmStore(Path(self.temp.name))
        self.control = ControlChannel(self.store.directory)

    def tearDown(self):
        self.temp.cleanup()

    def test_stop_request_ends_run_and_cleans_up(self):
        seen_pid = []

        def on_sleep(_):
            seen_pid.append(self.control.daemon_pid())
            self.control.send("stop")

        Scheduler(self.store, clock=lambda: datetime(2026, 9, 14, 7, 30), sleep=on_sleep, control=self.control).run()
        self.assertEqual(seen_pid, [os.getpid()])
        self.assertIsNone(self.control.daemon_pid())
        self.assertFalse(self.control.daemon_running())

    def test_shutdown_outcome_stops_scheduler(self):
        now = datetime(2026, 9, 14, 7, 30)
        self.store.update(lambda alarms: alarms.append(Alarm("abcdefgh", "07:30:00", RepeatMode.ONCE, now)))
        scheduler = Scheduler(self.store, clock=lambda: now, alert=lambda _: AlertOutcome.SHUTDOWN)
        scheduler.tick()
        self.assertTrue(scheduler._stopped)


class LifecycleCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.store = AlarmStore(Path(self.temp.name))
        self.parser = build_parser()

    def tearDown(self):
        self.temp.cleanup()

    def run_cli(self, *argv):
        return run(self.parser.parse_args(list(argv)), store=self.store)

    def test_status_and_stop_without_daemon(self):
        self.assertIn("not running", self.run_cli("status"))
        with self.assertRaises(DaemonLifecycleError):
            stop_daemon(self.store.directory)

    def test_dismiss_without_daemon_fails(self):
        with self.assertRaises(DaemonLifecycleError):
            self.run_cli("dismiss")

    def test_start_refuses_when_daemon_running(self):
        with FileLock(ControlChannel(self.store.directory).lock_path, blocking=False):
            with self.assertRaises(DaemonAlreadyRunningError):
                self.run_cli("start")

    def test_start_status_stop_real_background_daemon(self):
        self.assertIn("started in the background", self.run_cli("start"))
        pid = ControlChannel(self.store.directory).daemon_pid()
        self.assertTrue(process_alive(pid))
        try:
            self.assertIn("Daemon: running", self.run_cli("status"))
        finally:
            self.assertIn("stopped", self.run_cli("stop"))
        # stop must not return while the process still holds daemon.log open (Windows can't clean it up)
        self.assertFalse(process_alive(pid))
        self.assertIn("not running", self.run_cli("status"))

    def test_process_alive(self):
        self.assertTrue(process_alive(os.getpid()))
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait()
        self.assertFalse(process_alive(child.pid))


if __name__ == "__main__":
    unittest.main()
