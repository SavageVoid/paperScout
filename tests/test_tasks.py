import tempfile
import unittest
from datetime import datetime, timezone, timedelta

from docpulse.config import ArxivSettings
from docpulse.ingest.bronze import BronzeVerificationError, partition_prefix
from docpulse.ingest.tasks import ingest_date_for, run_bronze_ingest, run_verify_bronze
from docpulse.storage import LocalStore
from fakes import FakeArxiv, make_dataset

UTC = timezone.utc


class _LossyDatetime(datetime):
    """Mimics the Airflow/pendulum failure: arithmetic and astimezone() return values that
    have lost their timezone. Plain stdlib datetimes never do this, which is why it slipped
    past the first test suite."""

    def astimezone(self, tz=None):
        return datetime.astimezone(self, tz).replace(tzinfo=None)

    def __sub__(self, other):
        result = datetime.__sub__(self, other)
        return result.replace(tzinfo=None) if isinstance(result, datetime) else result


def _lossy(y, m, d, h=0):
    return _LossyDatetime(y, m, d, h, tzinfo=timezone.utc)


class TaskTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = LocalStore(self._tmp.name)
        self.settings = ArxivSettings(page_size=50, max_pages=10, lookback_hours=6)
        # one paper every 30 min, newest just after the interval end
        self.data = make_dataset(300, datetime(2026, 10, 27, 0, 20, tzinfo=UTC), step_minutes=30)
        self.start = datetime(2026, 10, 26, tzinfo=UTC)
        self.end = datetime(2026, 10, 27, tzinfo=UTC)

    def ingest(self):
        return run_bronze_ingest(self.start, self.end, settings=self.settings, store=self.store,
                                 client=FakeArxiv(self.data))

    def test_partition_date_is_logical_date_in_utc(self):
        # a non-UTC pendulum-style tz must not shift the partition
        plus5 = timezone(timedelta(hours=5))
        self.assertEqual(ingest_date_for(datetime(2026, 10, 26, 4, 0, tzinfo=plus5)).isoformat(), "2026-10-25")

    def test_lookback_widens_the_window_start(self):
        summary = self.ingest()
        # 6h lookback + 24h interval = 30h = 60 papers at 30 min spacing (00:20 newest is outside the end)
        self.assertEqual(summary["ingest_date"], "2026-10-26")
        self.assertEqual(summary["n_entries_in_window"], 60)

    def test_ingest_then_verify_roundtrip(self):
        summary = self.ingest()
        self.assertEqual(run_verify_bronze(summary, store=self.store), summary)

    def test_verify_fails_if_partition_changed_after_ingest(self):
        summary = self.ingest()
        self.store.put_bytes(partition_prefix(datetime(2026, 10, 26).date()) + "page-00000.xml", b"x")
        with self.assertRaises(BronzeVerificationError):
            run_verify_bronze(summary, store=self.store)

    def test_env_extends_skipped_announcements(self):
        from datetime import date
        s = ArxivSettings.from_env({"ARXIV_SKIPPED_ANNOUNCEMENTS": "2026-11-26, 2026-12-25"})
        self.assertIn(date(2026, 10, 28), s.skipped_announcements)   # known one is kept
        self.assertIn(date(2026, 12, 25), s.skipped_announcements)

    def test_default_lookback_covers_the_longest_normal_visibility_delay(self):
        # Fri 14:00 ET submissions only become public Mon 20:00 ET: 78 h later, plus our daily cadence.
        self.assertGreaterEqual(ArxivSettings().lookback_hours, 78 + 24)

    def test_airflow_style_datetimes_are_handled(self):
        summary = run_bronze_ingest(
            _lossy(2026, 10, 26), _lossy(2026, 10, 27), settings=self.settings,
            store=self.store, client=FakeArxiv(self.data),
        )
        self.assertEqual(summary["ingest_date"], "2026-10-26")
        self.assertEqual(summary["n_entries_in_window"], 60)
        self.assertEqual(ingest_date_for(_lossy(2026, 10, 26)).isoformat(), "2026-10-26")

    def test_naive_interval_rejected(self):
        with self.assertRaises(ValueError):
            run_bronze_ingest(datetime(2026, 10, 26), self.end, settings=self.settings,
                              store=self.store, client=FakeArxiv([]))

    def test_manual_trigger_with_empty_interval_rejected(self):
        with self.assertRaises(ValueError):
            run_bronze_ingest(self.end, self.end, settings=self.settings, store=self.store, client=FakeArxiv([]))


if __name__ == "__main__":
    unittest.main()
