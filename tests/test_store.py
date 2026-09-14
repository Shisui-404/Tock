from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tock.models import Alarm, RepeatMode
from tock.store import AlarmStore, StoreError


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.store = AlarmStore(Path(self.temporary.name))

    def tearDown(self):
        self.temporary.cleanup()

    def test_round_trip(self):
        original = Alarm("abcdefgh", "07:30:00", RepeatMode.ONCE, datetime(2026, 9, 15, 7, 30), label="Wake")
        self.store.update(lambda alarms: alarms.append(original))
        loaded = self.store.load()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].label, "Wake")
        self.assertEqual(loaded[0].next_fire_at, original.next_fire_at)

    def test_unknown_version_is_refused(self):
        self.store.path.write_text('{"version": 99, "alarms": []}', encoding="utf-8")
        with self.assertRaisesRegex(StoreError, "unsupported"):
            self.store.load()

    def test_corrupt_store_is_backed_up(self):
        self.store.path.write_text("not json", encoding="utf-8")
        with self.assertWarnsRegex(RuntimeWarning, "backed up"):
            self.assertEqual(self.store.load(), [])
        self.assertEqual(len(list(Path(self.temporary.name).glob("alarms.json.corrupt-*"))), 1)


if __name__ == "__main__":
    unittest.main()
