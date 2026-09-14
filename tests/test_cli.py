from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tock.cli import build_parser, run
from tock.store import AlarmStore


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.store = AlarmStore(Path(self.temp.name))
        self.now = datetime(2026, 9, 14, 10, 30)
        self.parser = build_parser()

    def tearDown(self):
        self.temp.cleanup()

    def test_add_and_list_relative_alarm(self):
        result = run(self.parser.parse_args(["add", "+10m", "--label", "Tea"]), store=self.store, now=self.now)
        self.assertIn("Added", result)
        self.assertIn("Tea", run(self.parser.parse_args(["list"]), store=self.store, now=self.now))

    def test_remove_accepts_unique_prefix(self):
        run(self.parser.parse_args(["add", "+10m"]), store=self.store, now=self.now)
        alarm_id = self.store.load()[0].id
        self.assertEqual(run(self.parser.parse_args(["remove", alarm_id[:4]]), store=self.store), f"Removed {alarm_id}")

    def test_weekly_alarm_records_requested_weekday(self):
        run(self.parser.parse_args(["add", "12:00", "--repeat", "weekly", "--day", "fri"]), store=self.store, now=self.now)
        self.assertEqual(self.store.load()[0].weekday, 4)

    def test_toggle_disables_then_enables(self):
        run(self.parser.parse_args(["add", "+10m"]), store=self.store, now=self.now)
        alarm_id = self.store.load()[0].id
        self.assertIn("Disabled", run(self.parser.parse_args(["toggle", alarm_id]), store=self.store, now=self.now))
        self.assertFalse(self.store.load()[0].enabled)
        self.assertIn("Enabled", run(self.parser.parse_args(["toggle", alarm_id]), store=self.store, now=self.now))


if __name__ == "__main__":
    unittest.main()
