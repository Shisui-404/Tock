from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tock.alert import AlertOutcome
from tock.models import Alarm, RepeatMode
from tock.scheduler import Scheduler
from tock.store import AlarmStore


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.store = AlarmStore(Path(self.temp.name))
        self.now = datetime(2026, 9, 14, 7, 30)
        self.alerted = []

    def tearDown(self):
        self.temp.cleanup()

    def scheduler(self):
        return Scheduler(self.store, clock=lambda: self.now, alert=lambda alarm: self.alerted.append(alarm.id) or AlertOutcome.STOP)

    def test_one_off_is_committed_before_alert(self):
        alarm = Alarm("abcdefgh", "07:30:00", RepeatMode.ONCE, self.now)
        self.store.update(lambda alarms: alarms.append(alarm))
        self.assertEqual(self.scheduler().tick(), ["abcdefgh"])
        self.assertEqual(self.alerted, ["abcdefgh"])
        saved = self.store.load()[0]
        self.assertIsNone(saved.next_fire_at)
        self.assertEqual(saved.last_fired_at, self.now)

    def test_daily_alarm_advances(self):
        alarm = Alarm("abcdefgh", "07:30:00", RepeatMode.DAILY, self.now)
        self.store.update(lambda alarms: alarms.append(alarm))
        self.scheduler().tick()
        self.assertEqual(self.store.load()[0].next_fire_at, self.now + timedelta(days=1))

    def test_snooze_is_persisted(self):
        alarm = Alarm("abcdefgh", "07:30:00", RepeatMode.ONCE, self.now)
        self.store.update(lambda alarms: alarms.append(alarm))
        scheduler = Scheduler(self.store, clock=lambda: self.now, snooze_minutes=5,
                              alert=lambda _: AlertOutcome.SNOOZE)
        scheduler.tick()
        self.assertEqual(self.store.load()[0].snoozed_until, self.now + timedelta(minutes=5))

    def test_stale_startup_alarm_is_skipped_after_grace_window(self):
        alarm = Alarm("abcdefgh", "07:00:00", RepeatMode.ONCE, self.now - timedelta(hours=1))
        self.store.update(lambda alarms: alarms.append(alarm))
        scheduler = Scheduler(self.store, clock=lambda: self.now, grace_minutes=15,
                              alert=lambda _: self.fail("stale alarm should not alert"), output=lambda _: None)
        self.assertEqual(scheduler.tick(), [])
        self.assertIsNone(self.store.load()[0].next_fire_at)


if __name__ == "__main__":
    unittest.main()
