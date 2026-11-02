"""Day 5: SearchDB.search_keyword, exercised against a scripted cursor.

The unit tests for the engine live in test_search_keyword.py and the policy tests in
test_search_modes.py. What is untested by either is the seam between them: that the
method runs the engine, then fetches metadata for exactly those ids, then merges in
the engine's order. That seam is what this file covers, without a database.

It binds the method to a duck-typed stand-in rather than constructing SearchDB, so
it does not depend on the constructor or on psycopg being reachable.
"""

from __future__ import annotations

import unittest

try:
    from docpulse.search.db import SearchDB
except Exception:  # pragma: no cover - import needs the search extras
    SearchDB = None

COLUMNS = ["arxiv_id", "title", "abstract", "categories", "published", "updated", "version"]


class FakeCursor:
    """Serves stems, then hits, then metadata, keyed on the SQL that arrives."""

    def __init__(self, stems, hits, meta, relaxed=None):
        self.stems = stems
        self.hits = hits
        self.meta = meta
        self.relaxed = hits if relaxed is None else relaxed
        self.calls: list[tuple[str, dict]] = []
        self._kind = None
        self.description = [(c,) for c in COLUMNS]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self.calls.append((flat, params or {}))
        if "tsvector_to_array(to_tsvector" in flat and "INTERSECT" not in flat:
            self._kind = "stems"
        elif "INTERSECT" in flat:
            self._kind = "relaxed"
        elif "m.fts @@" in flat:
            self._kind = "strict"
        else:
            self._kind = "meta"

    def fetchone(self):
        return (self.stems,)

    def fetchall(self):
        if self._kind == "strict":
            return list(self.hits)
        if self._kind == "relaxed":
            return list(self.relaxed)
        return list(self.meta)


class _Lock:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeDB:
    meta_table = "paper_meta"
    _lock = _Lock()

    def __init__(self, cursor):
        self._cursor_obj = cursor

    def _cursor(self):
        return self._cursor_obj


@unittest.skipIf(SearchDB is None, "docpulse.search.db not importable here")
class SearchKeywordMethodTests(unittest.TestCase):
    def call(self, cursor, **kwargs):
        bind = SearchDB.search_keyword.__get__(FakeDB(cursor), FakeDB)
        return bind(**kwargs)

    def strict_hits(self):
        # the AND arm returns (arxiv_id, title, score)
        return [("2401.1", "First", 4.0), ("2401.2", "Second", 2.0)]

    def relaxed_hits(self):
        # the OR arm also returns the coverage count; two stems, so 2 is full
        return [("2401.1", "First", 2, 4.0), ("2401.2", "Second", 1, 2.0)]

    def meta(self):
        return [
            ("2401.1", "First", "abs a", "cs.CL", "2026-10-01", "2026-10-28", 1),
            ("2401.2", "Second", "abs b", "cs.LG", "2026-10-02", "2026-10-27", 2),
        ]

    def test_engine_order_is_preserved_through_the_merge(self):
        cur = FakeCursor(["bert", "mask"], self.strict_hits(), self.meta())
        rows, report = self.call(cur, query="BERT masked", limit=10)
        self.assertEqual([r["arxiv_id"] for r in rows], ["2401.1", "2401.2"])
        self.assertEqual(report["mode"], "strict")
        self.assertEqual(report["n_exact"], 2)

    def test_the_fallback_never_claims_an_exact_match(self):
        cur = FakeCursor(["bert", "mask"], [], self.meta(), relaxed=self.relaxed_hits())
        rows, report = self.call(cur, query="BERT masked", limit=10)
        self.assertEqual(len(rows), 2)
        self.assertEqual(report["mode"], "relaxed")
        self.assertEqual(report["n_exact"], 0)
        self.assertEqual(report["coverage"][0], {"arxiv_id": "2401.1",
                                                 "matched": 2, "of": 2})

    def test_rows_match_the_vector_arms_shape(self):
        cur = FakeCursor(["bert"], self.strict_hits(), self.meta())
        rows, _ = self.call(cur, query="bert", limit=10)
        for key in ("arxiv_id", "title", "abstract", "categories", "published",
                    "updated", "version", "score"):
            self.assertIn(key, rows[0])

    def test_metadata_is_fetched_once_for_the_returned_ids(self):
        cur = FakeCursor(["bert"], self.strict_hits(), self.meta())
        self.call(cur, query="bert", limit=10)
        meta_calls = [p for s, p in cur.calls if "ANY(%(ids)s::text[])" in s]
        self.assertEqual(len(meta_calls), 1)
        self.assertEqual(meta_calls[0]["ids"], ["2401.1", "2401.2"])

    def test_a_hit_the_date_filter_removes_is_dropped(self):
        cur = FakeCursor(["bert"], self.strict_hits(), self.meta()[1:])
        rows, report = self.call(cur, query="bert", limit=10, since="2026-10-02")
        self.assertEqual([r["arxiv_id"] for r in rows], ["2401.2"])
        self.assertEqual(report["n_returned"], 1)

    def test_filters_are_forwarded_to_the_metadata_lookup(self):
        cur = FakeCursor(["bert"], self.strict_hits(), self.meta())
        self.call(cur, query="bert", limit=10, category="cs.CL",
                  since="2026-01-01", until="2027-01-01")
        params = [p for s, p in cur.calls if "ANY(%(ids)s::text[])" in s][0]
        self.assertEqual(params["category"], "cs.CL")
        self.assertEqual(params["since"], "2026-01-01")
        self.assertEqual(params["until"], "2027-01-01")

    def test_no_hits_means_no_metadata_lookup(self):
        cur = FakeCursor(["bert"], [], [])
        rows, report = self.call(cur, query="bert", limit=10)
        self.assertEqual(rows, [])
        self.assertEqual(report["mode"], "empty")
        self.assertFalse([s for s, _ in cur.calls if "ANY(%(ids)s::text[])" in s])

    def test_empty_string_filters_become_null(self):
        cur = FakeCursor(["bert"], self.strict_hits(), self.meta())
        self.call(cur, query="bert", limit=10, category="", since="", until="")
        params = [p for s, p in cur.calls if "ANY(%(ids)s::text[])" in s][0]
        self.assertIsNone(params["category"])
        self.assertIsNone(params["since"])
        self.assertIsNone(params["until"])


if __name__ == "__main__":
    unittest.main()
