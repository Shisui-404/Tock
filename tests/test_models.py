from datetime import datetime
import unittest

from tock.models import Alarm, RepeatMode, next_occurrence


def alarm(repeat, weekday=None):
    return Alarm("abcdefgh", "07:30:00", repeat, datetime(2026, 9, 14, 7, 30), weekday=weekday)


class ModelsTests(unittest.TestCase):
    def test_daily_is_strictly_after(self):
        self.assertEqual(next_occurrence(alarm(RepeatMode.DAILY), datetime(2026, 9, 14, 7, 30)), datetime(2026, 9, 15, 7, 30))

    def test_weekdays_skips_weekend(self):
        self.assertEqual(next_occurrence(alarm(RepeatMode.WEEKDAYS), datetime(2026, 9, 18, 8, 0)), datetime(2026, 9, 21, 7, 30))

    def test_weekly_rolls_forward(self):
        self.assertEqual(next_occurrence(alarm(RepeatMode.WEEKLY, 0), datetime(2026, 9, 14, 8, 0)), datetime(2026, 9, 21, 7, 30))


if __name__ == "__main__":
    unittest.main()
