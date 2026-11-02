"""Day 5 step 3b: the keyword engine, tested against a scripted cursor.

The database behaviour (which plan Postgres picks, how fast it is) is measured by
the probe on the real corpus. What is asserted here is the part that decides an
answer rather than a timing: when strict mode wins, when it falls back, and that
every query runs against the stored column instead of re-parsing the text.
"""

from __future__ import annotations

import unittest

from docpulse.search.keyword import (
    CATEGORY_PREDICATE,
    _relaxed_sql,
    _strict_sql,
    search_keyword,
    tokens,
)


class FakeCursor:
    """Plays back canned rows keyed on which SQL shape was executed."""

    def __init__(self, stems, strict_rows, relaxed_rows):
        self.stems = stems
        self.strict_rows = strict_rows
        self.relaxed_rows = relaxed_rows
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((" ".join(sql.split()), params or {}))

    def fetchone(self):
        return (self.stems,)

    def fetchall(self):
        last = self.calls[-1][0]
        if "INTERSECT" in last:
            return list(self.relaxed_rows)
        return list(self.strict_rows)

    def executed_kinds(self):
        return ["relaxed" if "INTERSECT" in sql else
                "stems" if "tsvector_to_array(to_tsvector" in sql and
                "INTERSECT" not in sql and "paper_meta" not in sql else
                "strict" for sql, _ in self.calls]


class DecisionTests(unittest.TestCase):
    def test_strict_hit_stops_there(self):
        cur = FakeCursor(["bert", "pretrain"], [("2401.1", "A", 1.5)], [("x", "y", 1, 0.1)])
        out = search_keyword(cur, "BERT pretraining")
        self.assertEqual(out["mode"], "strict")
        self.assertEqual(len(out["rows"]), 1)
        self.assertEqual(out["rows"][0]["coverage"], 2)
        self.assertEqual(out["rows"][0]["mode"], "strict")
        self.assertEqual(cur.executed_kinds(), ["stems", "strict"])

    def test_strict_miss_falls_back_to_relaxed(self):
        cur = FakeCursor(["bert", "pretrain", "mask"], [],
                         [("2401.2", "B", 2, 0.9), ("2401.3", "C", 1, 0.4)])
        out = search_keyword(cur, "BERT pretraining masked")
        self.assertEqual(out["mode"], "relaxed")
        self.assertEqual([r["coverage"] for r in out["rows"]], [2, 1])
        self.assertEqual(out["rows"][0]["total"], 3)
        self.assertEqual(cur.executed_kinds(), ["stems", "strict", "relaxed"])

    def test_both_empty_reports_empty(self):
        cur = FakeCursor(["protein"], [], [])
        out = search_keyword(cur, "protein zzz")
        self.assertEqual(out["mode"], "empty")
        self.assertEqual(out["rows"], [])

    def test_input_with_no_letters_never_touches_the_table(self):
        # tokens() is a shape check, not a stopword list: Postgres stays the only
        # authority on what a stopword is, so the two cannot drift apart
        cur = FakeCursor([], [], [])
        out = search_keyword(cur, "!!! ??")
        self.assertEqual(out["mode"], "empty")
        self.assertEqual(cur.calls, [])

    def test_postgres_side_stopwords_are_handled(self):
        # our token filter passed "however"; Postgres stemmed it away entirely
        cur = FakeCursor([], [], [])
        out = search_keyword(cur, "however")
        self.assertEqual(out["mode"], "empty")
        self.assertEqual(cur.executed_kinds(), ["stems"])


class ParameterTests(unittest.TestCase):
    def test_limit_is_bound_not_interpolated(self):
        cur = FakeCursor(["bert"], [("2401.1", "A", 1.0)], [])
        search_keyword(cur, "bert", limit=7, category="cs.CL")
        _kind, params = cur.calls[1]
        self.assertEqual(params["limit"], 7)
        self.assertEqual(params["category"], "cs.CL")

    def test_strict_query_is_anded_and_relaxed_is_ored(self):
        cur = FakeCursor(["bert", "pretrain"], [], [("2401.2", "B", 1, 0.1)])
        search_keyword(cur, "BERT pretraining")
        self.assertEqual(cur.calls[1][1]["tsq"], "'bert' & 'pretrain'")
        self.assertEqual(cur.calls[2][1]["tsq"], "'bert' | 'pretrain'")

    def test_quotes_in_a_lexeme_cannot_break_the_tsquery(self):
        cur = FakeCursor(["o'brien"], [], [])
        search_keyword(cur, "o'brien")
        self.assertEqual(cur.calls[1][1]["tsq"], "'o''brien'")

    def test_unknown_sort_is_rejected(self):
        cur = FakeCursor(["bert"], [], [])
        with self.assertRaises(ValueError):
            search_keyword(cur, "bert", sort="score")

    def test_relaxed_gets_a_candidate_cap(self):
        cur = FakeCursor(["bert"], [], [("2401.2", "B", 1, 0.1)])
        search_keyword(cur, "bert", cand=50)
        self.assertEqual(cur.calls[2][1]["cand"], 50)


class SqlTests(unittest.TestCase):
    def test_no_inline_to_tsvector_in_any_match_predicate(self):
        # the whole point of the migration: match the stored column
        for sql in (_strict_sql("relevance"), _relaxed_sql("relevance")):
            where = sql.split("WHERE", 1)[1]
            self.assertIn("m.fts @@", where)
            self.assertNotIn("to_tsvector", where)

    def test_category_filter_matches_day4(self):
        self.assertIn("string_to_array(coalesce(m.categories, ''), ' ')", CATEGORY_PREDICATE)
        for sql in (_strict_sql("relevance"), _relaxed_sql("relevance")):
            self.assertIn(CATEGORY_PREDICATE, sql)

    def test_relaxed_orders_by_coverage_before_rank(self):
        self.assertIn("ORDER BY coverage DESC, c.score DESC", _relaxed_sql("relevance"))

    def test_updated_sort_is_newest_first(self):
        self.assertIn("m.updated DESC NULLS LAST", _strict_sql("updated"))
        self.assertIn("c.updated DESC NULLS LAST", _relaxed_sql("updated"))

    def test_coverage_comes_from_the_stored_tsvector(self):
        sql = _relaxed_sql("relevance")
        self.assertIn("unnest(tsvector_to_array(c.fts))", sql)
        self.assertIn("INTERSECT", sql)

    def test_stems_are_passed_to_the_relaxed_arm(self):
        cur = FakeCursor(["bert", "pretrain"], [], [("2401.2", "B", 1, 0.1)])
        search_keyword(cur, "BERT pretraining")
        self.assertEqual(cur.calls[2][1]["stems"], ["bert", "pretrain"])

    def test_the_dictionary_is_never_applied_twice(self):
        # to_tsquery re-stems its input, and stemming is not idempotent (spars ->
        # spar), which silently searched for lexemes no document contains
        for sql in (_strict_sql("relevance"), _relaxed_sql("relevance")):
            self.assertNotIn("to_tsquery", sql)
            self.assertIn("%(tsq)s::tsquery", sql)

    def test_relaxed_coverage_uses_the_stems_it_was_given(self):
        sql = _relaxed_sql("relevance")
        self.assertIn("unnest(%(stems)s::text[])", sql)
        self.assertNotIn("to_tsvector('english'::regconfig, %(q)s) AS qv", sql)

    def test_all_statements_are_parameterised(self):
        for sql in (_strict_sql("relevance"), _relaxed_sql("relevance")):
            self.assertNotIn("%(q)s = ", sql)
            self.assertIn("%(tsq)s", sql)
            self.assertIn("%(limit)s", sql)


class TokenTests(unittest.TestCase):
    def test_shape_only_keeping_word_order(self):
        # stopwords survive here on purpose; STEMS_SQL drops them in Postgres
        self.assertEqual(tokens("the of a BERT model"), ["the", "of", "bert", "model"])

    def test_single_letters_and_punctuation_are_dropped(self):
        self.assertEqual(tokens("a b - c model!"), ["model"])

    def test_empty(self):
        self.assertEqual(tokens(""), [])
        self.assertEqual(tokens(None), [])


if __name__ == "__main__":
    unittest.main()
