from datetime import datetime, time
import unittest

from tock.timeparse import TimeParseError, parse_absolute, parse_clock, parse_relative


class TimeParseTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 14, 10, 30, 0)

    def test_parses_strict_clock_formats(self):
        self.assertEqual(parse_clock("07:30"), time(7, 30))
        self.assertEqual(parse_clock("07:30:15"), time(7, 30, 15))

    def test_rejects_malformed_clock_formats(self):
        for value in ("7:30", "07:3", "25:00", "07:60"):
            with self.subTest(value=value), self.assertRaises(TimeParseError):
                parse_clock(value)

    def test_relative_time(self):
        self.assertEqual(parse_relative("+10m", self.now), datetime(2026, 9, 14, 10, 40))

    def test_time_only_rolls_to_tomorrow(self):
        self.assertEqual(parse_absolute("10:30", None, self.now), datetime(2026, 9, 15, 10, 30))

    def test_explicit_past_datetime_is_rejected(self):
        with self.assertRaises(TimeParseError):
            parse_absolute("10:29", "2026-09-14", self.now)


if __name__ == "__main__":
    unittest.main()
