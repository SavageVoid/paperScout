import hashlib
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone

from docpulse.config import ArxivSettings
from docpulse.ingest.bronze import (
    BronzeIngestError,
    BronzeVerificationError,
    EmptyWindowError,
    IngestWindow,
    WindowTooDeepError,
    collect_pages,
    ingest_window,
    partition_prefix,
    verify_partition,
)
from docpulse.storage import LocalStore
from fakes import FakeArxiv, make_dataset

UTC = timezone.utc
NEWEST = datetime(2026, 10, 27, 0, 30, tzinfo=UTC)
WINDOW = IngestWindow(datetime(2026, 10, 26, 22, 0, tzinfo=UTC), datetime(2026, 10, 27, 0, 0, tzinfo=UTC))
D = date(2026, 10, 26)
FETCHED = datetime(2026, 10, 27, 3, 0, tzinfo=UTC)


class WindowTests(unittest.TestCase):
    def test_rejects_naive_and_inverted(self):
        with self.assertRaises(ValueError):
            IngestWindow(datetime(2026, 1, 1), datetime(2026, 1, 2, tzinfo=UTC))
        with self.assertRaises(ValueError):
            IngestWindow(datetime(2026, 1, 2, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC))

    def test_for_interval_applies_lookback_to_start_only(self):
        w = IngestWindow.for_interval(datetime(2026, 10, 26, tzinfo=UTC), datetime(2026, 10, 27, tzinfo=UTC), 6)
        self.assertEqual(w.start, datetime(2026, 10, 25, 18, tzinfo=UTC))
        self.assertEqual(w.end, datetime(2026, 10, 27, tzinfo=UTC))

    def test_half_open(self):
        self.assertTrue(WINDOW.contains(WINDOW.start))
        self.assertFalse(WINDOW.contains(WINDOW.end))


class BronzeTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = LocalStore(self._tmp.name)
        self.settings = ArxivSettings(page_size=10, max_pages=5, lookback_hours=0)

    def run_ingest(self, dataset, **kw):
        return ingest_window(FakeArxiv(dataset, **kw), self.store, self.settings, D, WINDOW, now=FETCHED)


class IngestTests(BronzeTestCase):
    def test_stops_once_a_page_reaches_the_window_start(self):
        # entries every 10 min from 00:30 back; window 22:00..00:00 -> in-window are 23:50 ... 22:00 = 12 entries
        manifest = self.run_ingest(make_dataset(100, NEWEST))
        self.assertEqual(manifest["n_pages"], 2)
        self.assertEqual(manifest["stop_reason"], "reached_window_start")
        self.assertEqual(manifest["n_entries_in_window"], 12)
        self.assertEqual(manifest["n_unique_papers_in_window"], 12)
        self.assertEqual(manifest["n_entries_total"], 20)

    def test_pages_are_stored_byte_for_byte(self):
        ds = make_dataset(100, NEWEST)
        manifest = self.run_ingest(ds)
        fake = FakeArxiv(ds)
        raw0 = fake.fetch_page(search_query="", start=0, max_results=10, sort_by="", sort_order="").raw
        stored = self.store.get_bytes(partition_prefix(D) + "page-00000.xml")
        self.assertEqual(stored, raw0)
        self.assertEqual(manifest["pages"][0]["sha256"], hashlib.sha256(raw0).hexdigest())

    def test_exhausted_results_when_window_is_older_than_everything(self):
        manifest = self.run_ingest(make_dataset(15, NEWEST))
        self.assertEqual(manifest["stop_reason"], "exhausted_results")
        self.assertEqual([p["n_entries"] for p in manifest["pages"]], [10, 5])

    def test_empty_result_set_is_refused_not_published(self):
        with self.assertRaises(EmptyWindowError):
            self.run_ingest([])
        self.assertEqual(self.store.list_keys(""), [])

    def test_manifest_records_window_and_is_written_last(self):
        manifest = self.run_ingest(make_dataset(100, NEWEST))
        on_disk = json.loads(self.store.get_bytes(partition_prefix(D) + "_manifest.json"))
        self.assertEqual(on_disk, manifest)
        self.assertEqual(on_disk["window"]["start"], "2026-10-26T22:00:00Z")
        self.assertTrue(on_disk["complete"])


class IdempotencyTests(BronzeTestCase):
    def snapshot(self):
        prefix = partition_prefix(D)
        return {k: self.store.get_bytes(k) for k in self.store.list_keys(prefix)}

    def test_rerun_gives_identical_objects(self):
        ds = make_dataset(100, NEWEST)
        self.run_ingest(ds)
        first = self.snapshot()
        self.run_ingest(ds)
        self.assertEqual(self.snapshot(), first)  # same keys, same bytes, nothing duplicated

    def test_rerun_with_fewer_pages_leaves_no_stale_pages(self):
        self.run_ingest(make_dataset(100, NEWEST))
        self.assertEqual(len(self.store.list_keys(partition_prefix(D))), 3)  # 2 pages + manifest
        self.run_ingest(make_dataset(5, NEWEST))  # now only one page of data
        self.assertEqual(len(self.store.list_keys(partition_prefix(D))), 2)  # 1 page + manifest
        verify_partition(self.store, D)

    def test_other_partitions_are_untouched(self):
        other = partition_prefix(date(2026, 10, 25)) + "page-00000.xml"
        self.store.put_bytes(other, b"yesterday")
        self.run_ingest(make_dataset(100, NEWEST))
        self.assertEqual(self.store.get_bytes(other), b"yesterday")

    def test_failed_fetch_keeps_previous_good_partition(self):
        self.run_ingest(make_dataset(100, NEWEST))
        before = self.snapshot()
        with self.assertRaises(RuntimeError):
            self.run_ingest(make_dataset(100, NEWEST), fail_on_call=2)
        self.assertEqual(self.snapshot(), before)
        verify_partition(self.store, D)


class GuardTests(BronzeTestCase):
    def test_stalled_feed_older_than_window_is_refused_and_keeps_previous_partition(self):
        self.run_ingest(make_dataset(100, NEWEST))
        before = {k: self.store.get_bytes(k) for k in self.store.list_keys("")}
        stale = make_dataset(50, datetime(2026, 10, 20, tzinfo=UTC))  # nothing newer than Oct 20
        with self.assertRaises(EmptyWindowError):
            self.run_ingest(stale)
        self.assertEqual({k: self.store.get_bytes(k) for k in self.store.list_keys("")}, before)

    def test_manifest_records_freshness_and_bytes(self):
        m = self.run_ingest(make_dataset(100, NEWEST))
        self.assertEqual(m["freshness"]["newest_updated_seen"], "2026-10-27T00:30:00Z")
        self.assertEqual(m["freshness"]["staleness_hours"], 2.5)  # fetched 03:00, newest 00:30
        self.assertEqual(m["n_bytes_total"], sum(p["bytes"] for p in m["pages"]))

    def test_max_pages_exceeded_raises_and_writes_nothing(self):
        self.settings = ArxivSettings(page_size=10, max_pages=1, lookback_hours=0)
        with self.assertRaises(BronzeIngestError):
            self.run_ingest(make_dataset(100, NEWEST))
        self.assertEqual(self.store.list_keys(""), [])

    def test_window_beyond_api_depth_raises(self):
        old = IngestWindow(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC))
        with self.assertRaises(WindowTooDeepError):
            collect_pages(FakeArxiv(make_dataset(100, NEWEST)), self.settings, old, max_offset=20)

    def test_page_request_is_clamped_to_api_depth(self):
        class Spy(FakeArxiv):
            sizes = []

            def fetch_page(self, **kw):
                Spy.sizes.append((kw["start"], kw["max_results"]))
                return super().fetch_page(**kw)

        old = IngestWindow(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC))
        with self.assertRaises(WindowTooDeepError):
            collect_pages(Spy(make_dataset(100, NEWEST)), self.settings, old, max_offset=25)
        self.assertEqual(Spy.sizes, [(0, 10), (10, 10), (20, 5)])


class VerifyTests(BronzeTestCase):
    def setUp(self):
        super().setUp()
        self.run_ingest(make_dataset(100, NEWEST))
        self.prefix = partition_prefix(D)

    def test_healthy_partition_passes(self):
        self.assertEqual(verify_partition(self.store, D)["n_pages"], 2)

    def test_detects_corrupted_page(self):
        self.store.put_bytes(self.prefix + "page-00001.xml", b"<feed/>")
        with self.assertRaises(BronzeVerificationError):
            verify_partition(self.store, D)

    def test_detects_missing_page(self):
        self.store._path(self.prefix + "page-00000.xml").unlink()
        with self.assertRaises(BronzeVerificationError):
            verify_partition(self.store, D)

    def test_detects_missing_manifest(self):
        self.store._path(self.prefix + "_manifest.json").unlink()
        with self.assertRaises(BronzeVerificationError):
            verify_partition(self.store, D)

    def test_detects_stray_object(self):
        self.store.put_bytes(self.prefix + "page-00007.xml", b"stale")
        with self.assertRaises(BronzeVerificationError):
            verify_partition(self.store, D)


if __name__ == "__main__":
    unittest.main()
