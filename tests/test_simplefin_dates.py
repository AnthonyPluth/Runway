"""SimpleFIN's timestamps to calendar days, wherever Runway runs (TZ decides "today", and defaults to America/New_York)."""
import os
import time
import unittest
from datetime import UTC, datetime
from unittest import mock

from runway import simplefin


def stamp(y, m, d, h=0, mi=0):
    return int(datetime(y, m, d, h, mi, tzinfo=UTC).timestamp())


class TimestampDayTests(unittest.TestCase):
    def read_in(self, tz, ts):
        with mock.patch.dict(os.environ, {"TZ": tz}):
            time.tzset()
            return simplefin._ts_to_date(ts)

    def tearDown(self):
        time.tzset()

    def test_a_midnight_utc_stamp_is_that_day_in_every_time_zone(self):
        monday = stamp(2026, 9, 28)
        for tz in ("UTC", "America/Chicago", "America/New_York", "America/Los_Angeles", "Pacific/Honolulu", "Europe/Berlin", "Asia/Tokyo"):
            with self.subTest(tz=tz):
                self.assertEqual(self.read_in(tz, monday), "2026-09-28")

    def test_a_bank_local_midnight_is_that_day_too(self):
        self.assertEqual(self.read_in("America/New_York", stamp(2026, 9, 28, 4)), "2026-09-28")
        self.assertEqual(self.read_in("America/Los_Angeles", stamp(2026, 9, 28, 7)), "2026-09-28")

    def test_a_stamp_with_a_real_time_is_the_day_it_was_where_runway_runs(self):
        afternoon = stamp(2026, 9, 28, 20, 0)
        self.assertEqual(self.read_in("America/Chicago", afternoon), "2026-09-28")
        self.assertEqual(self.read_in("America/Chicago", stamp(2026, 9, 28, 23, 30)), "2026-09-28")

    def test_nothing_or_junk_is_no_day(self):
        for bad in (None, "", 0, -5, "abc"):
            self.assertIsNone(simplefin._ts_to_date(bad))


if __name__ == "__main__":
    unittest.main()
