"""Day 5: the /search policy layer, tested without FastAPI or a database."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from docpulse.search.modes import (
    MODES,
    POOL_MAX,
    POOL_MIN,
    SORTS,
    apply_min_score,
    candidate_pool,
    keyword_report,
    merge_keyword_rows,
    order_by_updated,
)


def row(arxiv_id, score=0.5, updated=None):
    return {"arxiv_id": arxiv_id, "score": score, "updated": updated}


class CandidatePoolTests(unittest.TestCase):
    def test_small_limits_still_fetch_a_usable_pool(self):
        self.assertEqual(candidate_pool(5), POOL_MIN)
        self.assertEqual(candidate_pool(1), POOL_MIN)

    def test_pool_grows_with_the_limit(self):
        self.assertEqual(candidate_pool(50), 500)

    def test_pool_is_capped(self):
        self.assertEqual(candidate_pool(10_000), POOL_MAX)


class UpdatedOrderTests(unittest.TestCase):
    def test_newest_first(self):
        old = datetime(2026, 1, 1, tzinfo=timezone.utc)
        new = datetime(2026, 10, 28, tzinfo=timezone.utc)
        rows = [row("old", updated=old), row("new", updated=new)]
        self.assertEqual([r["arxiv_id"] for r in order_by_updated(rows, 2)], ["new", "old"])

    def test_rows_without_a_date_go_last(self):
        new = datetime(2026, 10, 28, tzinfo=timezone.utc)
        rows = [row("none"), row("new", updated=new)]
        self.assertEqual([r["arxiv_id"] for r in order_by_updated(rows, 2)], ["new", "none"])

    def test_two_undated_rows_do_not_compare_none(self):
        # sorting (False, None) against (False, None) must not raise
        rows = [row("a"), row("b")]
        self.assertEqual(len(order_by_updated(rows, 2)), 2)

    def test_ties_keep_their_similarity_order(self):
        same = datetime(2026, 10, 28, tzinfo=timezone.utc)
        rows = [row("first", updated=same), row("second", updated=same)]
        self.assertEqual([r["arxiv_id"] for r in order_by_updated(rows, 2)],
                         ["first", "second"])

    def test_limit_truncates_after_ordering(self):
        new = datetime(2026, 10, 28, tzinfo=timezone.utc)
        rows = [row("old", updated=datetime(2025, 1, 1, tzinfo=timezone.utc)),
                row("new", updated=new)]
        self.assertEqual([r["arxiv_id"] for r in order_by_updated(rows, 1)], ["new"])


class MinScoreTests(unittest.TestCase):
    def test_none_means_no_floor(self):
        rows = [row("a", 0.1), row("b", 0.9)]
        self.assertEqual(apply_min_score(rows, None), rows)

    def test_floor_drops_low_scores(self):
        rows = [row("a", 0.276), row("b", 0.496), row("c", 0.515)]
        kept = apply_min_score(rows, 0.4)
        self.assertEqual([r["arxiv_id"] for r in kept], ["b", "c"])

    def test_floor_is_inclusive(self):
        self.assertEqual(len(apply_min_score([row("a", 0.4)], 0.4)), 1)

    def test_string_scores_are_coerced(self):
        # psycopg can hand back numeric types; the comparison must not explode
        self.assertEqual(len(apply_min_score([{"arxiv_id": "a", "score": "0.9"}], 0.5)), 1)

    def test_zero_floor_drops_everything_negative(self):
        self.assertEqual(apply_min_score([row("a", -0.2), row("b", 0.0)], 0.0),
                         [{"arxiv_id": "b", "score": 0.0, "updated": None}])


class MergeTests(unittest.TestCase):
    def hits(self):
        return [
            {"arxiv_id": "2401.1", "score": 1.5, "coverage": 4, "total": 4, "mode": "strict"},
            {"arxiv_id": "2401.2", "score": 0.9, "coverage": 3, "total": 4, "mode": "strict"},
        ]

    def meta(self):
        return {
            "2401.1": {"arxiv_id": "2401.1", "title": "A", "categories": "cs.CL"},
            "2401.2": {"arxiv_id": "2401.2", "title": "B", "categories": "cs.LG"},
        }

    def test_engine_rank_is_preserved(self):
        rows = merge_keyword_rows(self.hits(), self.meta())
        self.assertEqual([r["arxiv_id"] for r in rows], ["2401.1", "2401.2"])

    def test_metadata_is_attached_and_score_comes_from_the_hit(self):
        rows = merge_keyword_rows(self.hits(), self.meta())
        self.assertEqual(rows[0]["title"], "A")
        self.assertEqual(rows[0]["score"], 1.5)

    def test_a_filtered_hit_is_dropped_not_returned_bare(self):
        meta = {"2401.2": self.meta()["2401.2"]}
        rows = merge_keyword_rows(self.hits(), meta)
        self.assertEqual([r["arxiv_id"] for r in rows], ["2401.2"])

    def test_rows_carry_the_same_keys_as_the_vector_arm(self):
        rows = merge_keyword_rows(self.hits(), self.meta())
        for key in ("arxiv_id", "title", "categories", "score"):
            self.assertIn(key, rows[0])
        self.assertNotIn("coverage", rows[0])

    def test_empty_hits(self):
        self.assertEqual(merge_keyword_rows([], {}), [])


class ReportTests(unittest.TestCase):
    def test_strict_reports_how_many_were_exact(self):
        found = {"mode": "strict", "stems": ["bert", "mask"],
                 "rows": [{"arxiv_id": "a", "coverage": 2, "total": 2}]}
        report = keyword_report(found, [{"arxiv_id": "a"}])
        self.assertEqual(report["mode"], "strict")
        self.assertEqual(report["n_exact"], 1)
        self.assertEqual(report["n_returned"], 1)
        self.assertEqual(report["coverage"][0]["matched"], 2)

    def test_relaxed_never_claims_an_exact_match(self):
        found = {"mode": "relaxed", "stems": ["bert"],
                 "rows": [{"arxiv_id": "a", "coverage": 1, "total": 3}]}
        report = keyword_report(found, [{"arxiv_id": "a"}])
        self.assertEqual(report["mode"], "relaxed")
        self.assertEqual(report["n_exact"], 0)

    def test_empty_result(self):
        report = keyword_report({"mode": "empty", "stems": [], "rows": []}, [])
        self.assertEqual(report, {"mode": "empty", "stems": [], "n_exact": 0,
                                  "n_returned": 0, "coverage": []})

    def test_missing_keys_do_not_raise(self):
        report = keyword_report({}, [])
        self.assertEqual(report["mode"], "empty")


class VocabularyTests(unittest.TestCase):
    def test_public_vocabularies(self):
        self.assertEqual(MODES, ("vector", "keyword"))
        self.assertEqual(SORTS, ("relevance", "updated"))


if __name__ == "__main__":
    unittest.main()
