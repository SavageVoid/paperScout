"""arXiv's public announcement schedule, used to judge whether the API feed is current.

Per arXiv's published schedule, submissions received up to 14:00 ET are made public at the
20:00 ET announcement on Sun/Mon/Tue/Wed/Thu (nothing on Fri/Sat; the Sunday announcement
covers Thu 14:00 - Fri 14:00). A paper's ``updated`` timestamp is its *submission* time, so
it only becomes visible in the API hours to days later (up to ~78 h over a normal weekend,
longer around holidays). The newest ``updated`` value visible at any moment is therefore the
14:00 ET cutoff of the most recent announcement.

Announcement days arXiv has said it will skip (holidays, maintenance) are listed in
``KNOWN_SKIPPED_ANNOUNCEMENTS`` and can be extended with ARXIV_SKIPPED_ANNOUNCEMENTS. Anything not
listed is unmodelled, so a "behind schedule" result is a warning signal, not proof of an outage.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from docpulse.timeutil import as_utc

ET = ZoneInfo("America/New_York")
_ANNOUNCE_WEEKDAYS = frozenset({6, 0, 1, 2, 3})  # Sun, Mon, Tue, Wed, Thu (Mon=0)
# ET dates on which arXiv announced it would NOT announce. Papers received meanwhile are announced at
# the next regular announcement. Sources: arXiv blog, "Scheduled Database Maintenance | October 28th, 2026".
KNOWN_SKIPPED_ANNOUNCEMENTS: frozenset[date] = frozenset({date(2026, 10, 28)})
API_GRACE_HOURS = 3.0  # arXiv's own bulk metadata refresh trails the announcement by a few hours
BEHIND_TOLERANCE_HOURS = 2.0  # a whole cycle is >= 24 h, so 2 h cleanly separates "late" from "normal"


def latest_announced_cutoff(
    now: datetime, skipped: frozenset[date] = KNOWN_SKIPPED_ANNOUNCEMENTS
) -> datetime:
    """UTC instant of the submission cutoff covered by the latest announcement at or before ``now``."""
    now_et = as_utc(now).astimezone(ET)
    for back in range(15):
        day = (now_et - timedelta(days=back)).date()
        if day.weekday() not in _ANNOUNCE_WEEKDAYS or day in skipped:
            continue
        announce = datetime(day.year, day.month, day.day, 20, 0, tzinfo=ET)
        if announce <= now_et:
            cutoff_day = day - timedelta(days=2) if day.weekday() == 6 else day
            cutoff = datetime(cutoff_day.year, cutoff_day.month, cutoff_day.day, 14, 0, tzinfo=ET)
            return cutoff.astimezone(timezone.utc)
    raise RuntimeError("no announcement found in the previous 15 days")  # unreachable in practice


def freshness(
    newest_seen: datetime | None,
    fetched_at: datetime,
    skipped: frozenset[date] = KNOWN_SKIPPED_ANNOUNCEMENTS,
) -> dict:
    """Compare the newest paper the API returned with what the schedule says should be visible."""
    expected = latest_announced_cutoff(as_utc(fetched_at) - timedelta(hours=API_GRACE_HOURS), skipped)
    behind = newest_seen is None or newest_seen < expected - timedelta(hours=BEHIND_TOLERANCE_HOURS)
    return {
        "newest_updated_seen": newest_seen.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        if newest_seen else None,
        "expected_newest_cutoff": expected.isoformat().replace("+00:00", "Z"),
        "staleness_hours": None if newest_seen is None
        else round((as_utc(fetched_at) - newest_seen).total_seconds() / 3600, 1),
        "behind_schedule": bool(behind),
    }
