import unittest
from datetime import datetime, timezone

from docpulse.ingest.fingerprint import entries_fingerprint
from fakes import feed_xml, make_dataset

NEWEST = datetime(2026, 10, 27, 17, 59, 59, tzinfo=timezone.utc)


def pages_of(dataset, size, envelope_time="2026-10-29T18:24:49Z"):
    out = []
    for start in range(0, len(dataset), size):
        raw = feed_xml(dataset[start : start + size], start=start, total=len(dataset), per_page=size)
        out.append(raw.replace(b"2026-10-27T00:00:00-04:00", envelope_time.encode()))
    return out


class FingerprintTests(unittest.TestCase):
    def setUp(self):
        self.data = make_dataset(120, NEWEST)

    def test_ignores_feed_level_response_timestamp(self):
        a = pages_of(self.data, 40, "2026-10-29T18:24:49Z")
        b = pages_of(self.data, 40, "2026-10-29T23:52:41Z")
        self.assertNotEqual(a, b)  # raw bytes really do differ...
        self.assertEqual(entries_fingerprint(a), entries_fingerprint(b))  # ...the fingerprint does not

    def test_ignores_page_boundaries_and_order(self):
        self.assertEqual(entries_fingerprint(pages_of(self.data, 40)), entries_fingerprint(pages_of(self.data, 25)))
        self.assertEqual(entries_fingerprint(pages_of(self.data, 40)),
                         entries_fingerprint(list(reversed(pages_of(self.data, 40)))))

    def test_duplicate_entry_across_pages_counts_once(self):
        with_overlap = pages_of(self.data[:60], 60) + pages_of(self.data[59:], 61)  # entry 59 appears twice
        fp, n = entries_fingerprint(with_overlap)
        self.assertEqual(n, 120)
        self.assertEqual(fp, entries_fingerprint(pages_of(self.data, 40))[0])

    def test_detects_a_changed_or_missing_entry(self):
        base = entries_fingerprint(pages_of(self.data, 40))[0]
        self.assertNotEqual(base, entries_fingerprint(pages_of(self.data[:-1], 40))[0])
        revised = list(self.data)
        revised[3] = (revised[3][0].replace("v1", "v2"), revised[3][1])
        self.assertNotEqual(base, entries_fingerprint(pages_of(revised, 40))[0])


if __name__ == "__main__":
    unittest.main()
