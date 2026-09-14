from base64 import b64decode
from datetime import datetime
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock
import unittest

from tock.alert import AlertOutcome, AlertSession
from tock.compat import notify, sound
from tock.compat.notify import DesktopNotifier, parse_response
from tock.compat.windows import ps_quote
from tock.control import ControlChannel
from tock.models import Alarm, RepeatMode
from tests.test_control import NoKeyboard, SilentPlayer


class FakeNotifier:
    instances = []

    def __init__(self, *, timeout):
        self.timeout = timeout
        self.error = None
        self.shown = None
        self.closed = False
        self.clicks = iter([None, "snooze"])
        FakeNotifier.instances.append(self)

    def show(self, title, message):
        self.shown = (title, message)

    def poll(self):
        return next(self.clicks)

    def close(self):
        self.closed = True


class BackendSelectionTests(unittest.TestCase):
    def which(self, *available):
        return mock.patch("shutil.which", side_effect=lambda name: f"/bin/{name}" if name in available else None)

    def test_wsl_prefers_windows_popup_and_sound(self):
        with mock.patch.object(notify, "powershell", return_value="powershell.exe"), \
                mock.patch.object(sound, "powershell", return_value="powershell.exe"), \
                mock.patch.object(sound.sys, "platform", "linux"), self.which("zenity"):
            self.assertEqual(notify.selected_notifier(), "windows popup")
            self.assertEqual(sound.selected_backend(), "powershell")

    def test_linux_desktop_falls_back_to_notify_send(self):
        with mock.patch.object(notify, "powershell", return_value=None), \
                mock.patch.object(notify.sys, "platform", "linux"), self.which("notify-send"):
            self.assertEqual(notify.selected_notifier(), "notify-send")

    def test_nothing_available(self):
        with mock.patch.object(notify, "powershell", return_value=None), \
                mock.patch.object(notify.sys, "platform", "linux"), self.which():
            self.assertEqual(notify.selected_notifier(), "none")


class CommandTests(unittest.TestCase):
    def test_windows_popup_embeds_quoted_label(self):
        with mock.patch.object(notify, "powershell", return_value="powershell.exe"):
            command = DesktopNotifier(timeout=60, backend="windows popup").command("Rob's tea", "Alarm at 07:30")
        script = b64decode(command[-1]).decode("utf-16-le")
        self.assertIn("$heading.Text = 'Rob''s tea'", script)
        self.assertIn("$timer.Interval = 60000", script)

    def test_osascript_passes_text_as_arguments(self):
        command = DesktopNotifier(timeout=60, backend="osascript").command('Say "hi"', "Alarm at 07:30")
        self.assertEqual(command[-3:], ['Say "hi"', "Alarm at 07:30", "60"])

    def test_none_backend_has_no_command(self):
        notifier = DesktopNotifier(backend="none")
        self.assertIsNone(notifier.command("Tea", "Alarm"))
        notifier.show("Tea", "Alarm")
        self.assertFalse(notifier.active)
        self.assertIsNone(notifier.poll())

    def test_ps_quote_doubles_single_quotes(self):
        self.assertEqual(ps_quote("it's"), "'it''s'")

    def test_parse_responses(self):
        self.assertEqual(parse_response("windows popup", 0, "snooze\r\n"), "snooze")
        self.assertIsNone(parse_response("windows popup", 0, "timeout\n"))
        self.assertEqual(parse_response("osascript", 0, "dismiss\n"), "dismiss")
        self.assertEqual(parse_response("zenity", 1, "Snooze\n"), "snooze")
        self.assertEqual(parse_response("zenity", 0, ""), "dismiss")
        self.assertIsNone(parse_response("zenity", 5, ""))
        self.assertIsNone(parse_response("notify-send", 0, ""))


class AlertPopupTests(unittest.TestCase):
    def test_popup_shows_label_and_click_snoozes(self):
        FakeNotifier.instances.clear()
        with TemporaryDirectory() as temp:
            directory = Path(temp)
            session = AlertSession(directory, ring_timeout=60, stream=StringIO(), sleep=lambda _: None,
                                   control=ControlChannel(directory), poller=NoKeyboard(), player_factory=SilentPlayer,
                                   notifier_factory=FakeNotifier)
            alarm = Alarm("abcdefgh", "07:30:00", RepeatMode.ONCE, datetime(2026, 9, 14, 7, 30), label="Tea")
            self.assertIs(session.run(alarm), AlertOutcome.SNOOZE)
        popup = FakeNotifier.instances[0]
        self.assertEqual(popup.shown, ("Tea", "Alarm at 07:30"))
        self.assertEqual(popup.timeout, 60)
        self.assertTrue(popup.closed)


if __name__ == "__main__":
    unittest.main()
