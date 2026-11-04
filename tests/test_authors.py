"""Day 6: the author engine, tested without a database.

Same approach as test_search_keyword.py: a duck-typed cursor records the SQL and
the parameters, so the assertions are about statements and binding, not about a
live Postgres. The live behaviour was verified separately against the real corpus
(LeCun -> 2610.39302, Wei Wang -> 10 rows).
"""

from __future__ import annotations

import unittest

from docpulse.search.authors import (
    COUNT_SQL,
    EXACT_SQL,
    PARTIAL_SQL,
    normalise,
    search_author,
    tokens,
)


class FakeCursor:
    """Returns queued responses in order and records every call."""

    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls: list[tuple[str, dict | None]] = []

    def execute(self, sql, params=None):
        self.calls.append((" ".join(sql.split()), params))

    def fetchall(self):
        return self.responses.pop(0)

    def fetchone(self):
        item = self.responses.pop(0)
        return item[0] if isinstance(item, list) else item


def hit(arxiv_id="2610.39302", title="Proving...", authors=None, score=1.0):
    return (arxiv_id, title, authors or ["Randall Balestriero", "Yann LeCun"], score)


class NormaliseTests(unittest.TestCase):
    def test_collapses_whitespace_and_lowercases(self):
        self.assertEqual(normalise("  Yann   LeCun "), "yann lecun")

    def test_none_and_empty_are_empty(self):
        self.assertEqual(normalise(None), "")
        self.assertEqual(normalise("   "), "")

    def test_accents_are_preserved_not_folded(self):
        # Folding would be a separate decision: it needs unaccent, and a name
        # typed without its accent is a different failure with its own measure.
        self.assertEqual(normalise("Hervé Panetto"), "hervé panetto")


class TokenTests(unittest.TestCase):
    def test_single_letters_and_punctuation_are_dropped(self):
        self.assertEqual(tokens(" ! "), [])
        self.assertEqual(tokens("x"), [])

    def test_hyphenated_and_apostrophed_names_survive(self):
        self.assertEqual(tokens("Jean-Pierre O'Neil"), ["jean-pierre", "o'neil"])

    def test_empty(self):
        self.assertEqual(tokens(""), [])
        self.assertEqual(tokens(None), [])


class ExactArmTests(unittest.TestCase):
    def test_exact_hit_stops_there(self):
        cur = FakeCursor([[hit()]])
        found = search_author(cur, "Yann LeCun")
        self.assertEqual(found["mode"], "exact")
        self.assertEqual(len(cur.calls), 1)

    def test_uses_case_insensitive_containment(self):
        cur = FakeCursor([[hit()]])
        search_author(cur, "Yann LeCun")
        self.assertIn("authors_ci @> ARRAY[%(key)s]", cur.calls[0][0])

    def test_key_is_the_normalised_name(self):
        cur = FakeCursor([[hit()]])
        search_author(cur, "  YANN   lecun ")
        self.assertEqual(cur.calls[0][1]["key"], "yann lecun")

    def test_no_english_config_anywhere(self):
        # 'english' would stem Harris to harri and split hyphenated names.
        for sql in (EXACT_SQL, PARTIAL_SQL, COUNT_SQL):
            self.assertNotIn("english", sql)

    def test_newest_first_because_every_hit_matches_equally(self):
        self.assertIn("ORDER BY m.updated DESC NULLS LAST", EXACT_SQL)

    def test_authors_are_returned_in_their_original_order(self):
        cur = FakeCursor([[hit(authors=["Randall Balestriero", "Yann LeCun"])]])
        found = search_author(cur, "Yann LeCun")
        self.assertEqual(found["rows"][0]["authors"],
                         ["Randall Balestriero", "Yann LeCun"])


class PartialFallbackTests(unittest.TestCase):
    def test_exact_miss_falls_back_to_partial(self):
        cur = FakeCursor([[], [("2407.1", "T", ["Yann LeCun"], 0.0607)]])
        found = search_author(cur, "lecun")
        self.assertEqual(found["mode"], "partial")
        self.assertEqual(len(cur.calls), 2)

    def test_partial_uses_the_simple_dictionary(self):
        cur = FakeCursor([[], [("2407.1", "T", ["Yann LeCun"], 0.06)]])
        search_author(cur, "lecun")
        self.assertIn("authors_tsv @@ plainto_tsquery('simple', %(key)s)", cur.calls[1][0])

    def test_partial_keeps_its_score(self):
        cur = FakeCursor([[], [("2407.1", "T", ["Yann LeCun"], 0.0607)]])
        found = search_author(cur, "lecun")
        self.assertEqual(found["rows"][0]["score"], 0.0607)

    def test_nothing_anywhere_reports_empty(self):
        cur = FakeCursor([[], []])
        found = search_author(cur, "Nobody Atall")
        self.assertEqual(found["mode"], "empty")
        self.assertEqual(found["rows"], [])


class SaturationTests(unittest.TestCase):
    def test_no_count_when_the_answer_is_short(self):
        cur = FakeCursor([[hit()]])
        found = search_author(cur, "Yann LeCun")
        self.assertIsNone(found["total_matches"])
        self.assertEqual(len(cur.calls), 1)

    def test_count_runs_when_the_limit_is_reached(self):
        rows = [hit(arxiv_id=f"x{i}", authors=["Wei Wang"]) for i in range(3)]
        cur = FakeCursor([rows, [(200,)]])
        found = search_author(cur, "Wei Wang", limit=3)
        self.assertEqual(found["total_matches"], 200)
        self.assertIn("count(*)", cur.calls[1][0])

    def test_count_can_be_switched_off(self):
        rows = [hit(arxiv_id=f"x{i}", authors=["Wei Wang"]) for i in range(3)]
        cur = FakeCursor([rows])
        found = search_author(cur, "Wei Wang", limit=3, count_saturated=False)
        self.assertIsNone(found["total_matches"])
        self.assertEqual(len(cur.calls), 1)


class SafetyTests(unittest.TestCase):
    def test_junk_input_never_reaches_the_database(self):
        cur = FakeCursor([])
        found = search_author(cur, " !!! ")
        self.assertEqual(found["mode"], "empty")
        self.assertEqual(cur.calls, [])

    def test_the_name_is_bound_never_interpolated(self):
        cur = FakeCursor([[hit()]])
        search_author(cur, "Yann LeCun")
        sql, params = cur.calls[0]
        self.assertNotIn("Yann", sql)
        self.assertNotIn("yann", sql)
        self.assertEqual(params["key"], "yann lecun")

    def test_filters_are_bound(self):
        cur = FakeCursor([[hit()]])
        search_author(cur, "Yann LeCun", category="cs.LG",
                      since="2026-01-01", until="2026-11-01", limit=5)
        params = cur.calls[0][1]
        self.assertEqual(params["category"], "cs.LG")
        self.assertEqual(params["since"], "2026-01-01")
        self.assertEqual(params["until"], "2026-11-01")
        self.assertEqual(params["limit"], 5)

    def test_category_predicate_matches_day4(self):
        self.assertIn("string_to_array(coalesce(m.categories, ''), ' ')", EXACT_SQL)

    def test_date_window_uses_published_like_the_vector_arm(self):
        self.assertIn("m.published >= %(since)s", EXACT_SQL)


if __name__ == "__main__":
    unittest.main()
