import unittest
from datetime import datetime, timedelta, timezone

from docpulse.ingest.schedule import freshness, latest_announced_cutoff

UTC = timezone.utc


def z(*a):
    return datetime(*a, tzinfo=UTC)


class CutoffTests(unittest.TestCase):
    def test_midweek_afternoon(self):
        # Thu 14:24 EDT: Wed 20:00 EDT announcement has happened, Thu 20:00 has not
        self.assertEqual(latest_announced_cutoff(z(2026, 10, 29, 18, 24)), z(2026, 10, 28, 18, 0))

    def test_just_before_and_after_an_announcement(self):
        self.assertEqual(latest_announced_cutoff(z(2026, 10, 29, 23, 59)), z(2026, 10, 28, 18, 0))
        self.assertEqual(latest_announced_cutoff(z(2026, 10, 30, 0, 0)), z(2026, 10, 29, 18, 0))

    def test_weekend_still_points_at_thursday_cutoff(self):
        # Sat 08:00 EDT: no announcements Fri/Sat, so the newest is Thursday's cutoff
        self.assertEqual(latest_announced_cutoff(z(2026, 10, 31, 12, 0)), z(2026, 10, 29, 18, 0))

    def test_sunday_announcement_covers_friday_cutoff_across_dst_change(self):
        # DST ends Sun 1 Nov 2026. Sun 20:00 EST = Mon 01:00 UTC.
        self.assertEqual(latest_announced_cutoff(z(2026, 11, 1, 23, 59)), z(2026, 10, 29, 18, 0))
        self.assertEqual(latest_announced_cutoff(z(2026, 11, 2, 1, 0)), z(2026, 10, 30, 18, 0))  # Fri 14:00 EDT

    def test_monday_announcement_in_winter_time(self):
        # Mon 20:00 EST = Tue 01:00 UTC; Monday cutoff 14:00 EST = 19:00 UTC
        self.assertEqual(latest_announced_cutoff(z(2026, 11, 3, 1, 0)), z(2026, 11, 2, 19, 0))


class FreshnessTests(unittest.TestCase):
    def test_replay_of_the_observed_stall_is_flagged(self):
        # Real observation: Thu 2026-10-29 18:24Z, API newest entry was 2026-10-27T17:59:59Z
        f = freshness(z(2026, 10, 27, 17, 59, 59), z(2026, 10, 29, 18, 24))
        self.assertTrue(f["behind_schedule"])
        self.assertEqual(f["expected_newest_cutoff"], "2026-10-28T18:00:00Z")

    def test_healthy_feed_is_not_flagged(self):
        f = freshness(z(2026, 10, 28, 17, 59, 59), z(2026, 10, 29, 18, 24))
        self.assertFalse(f["behind_schedule"])

    def test_long_weekend_gap_is_normal(self):
        # Sunday 18:00 EST, just before the Sunday announcement: newest is still Thursday's cutoff,
        # 77 h stale, and that is exactly as scheduled (the longest normal gap)
        f = freshness(z(2026, 10, 29, 17, 59, 59), z(2026, 11, 1, 23, 0))
        self.assertFalse(f["behind_schedule"])
        self.assertGreater(f["staleness_hours"], 76)

    def test_api_grace_period_right_after_an_announcement(self):
        # 01:00Z Fri, one hour after Thu's announcement: previous cycle is still acceptable
        f = freshness(z(2026, 10, 28, 17, 59, 59), z(2026, 10, 30, 1, 0))
        self.assertFalse(f["behind_schedule"])

    def test_no_entries_is_behind(self):
        self.assertTrue(freshness(None, z(2026, 10, 29, 18, 24))["behind_schedule"])


if __name__ == "__main__":
    unittest.main()
