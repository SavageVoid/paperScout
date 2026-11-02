"""Day 5 step 1: the probe's own logic, so a bad measurement cannot look like data.

The probe's SQL is validated by running it (step 1, on the real database); what is
testable here is everything around it — the fusion maths and the SQL rendering,
both of which decide whether the numbers that come back mean anything.
"""

from __future__ import annotations

import importlib.util
import pathlib
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "day5_fts_probe.py"
_spec = importlib.util.spec_from_file_location("day5_fts_probe_under_test", SCRIPT)
assert _spec and _spec.loader
probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe)


class RrfTests(unittest.TestCase):
    def test_document_in_both_arms_beats_one_in_a_single_arm(self):
        fused = probe.rrf([["a", "b"], ["b", "c"]])
        self.assertEqual(list(fused)[0], "b")

    def test_score_is_sum_of_reciprocal_ranks(self):
        fused = probe.rrf([["a"], ["a"]], k=60)
        self.assertAlmostEqual(fused["a"], 2 * (1 / 61), places=9)

    def test_rank_one_of_one_list_only(self):
        self.assertAlmostEqual(probe.rrf([["x"]], k=60)["x"], 1 / 61, places=9)

    def test_disjoint_arms_interleave(self):
        fused = probe.rrf([["a", "b"], ["c", "d"]])
        self.assertEqual(set(fused), {"a", "b", "c", "d"})
        self.assertEqual(list(fused)[:2], ["a", "c"])  # both rank 1 -> tie broken by insertion

    def test_duplicates_within_an_arm_do_not_double_count(self):
        # a buggy arm returning the same id twice must not inflate its score
        fused = probe.rrf([["a", "a"]])
        self.assertAlmostEqual(fused["a"], 1 / 61 + 1 / 62, places=9)

    def test_empty_inputs(self):
        self.assertEqual(probe.rrf([]), {})
        self.assertEqual(probe.rrf([[], []]), {})

    def test_k_changes_the_flatness_not_the_order(self):
        lists = [["a", "b"], ["b", "c"]]
        self.assertEqual(list(probe.rrf(lists, k=1)), list(probe.rrf(lists, k=60)))


class SqlRenderTests(unittest.TestCase):
    def render(self):
        return {"table": "paper_embeddings", "meta": "paper_meta", "idcol": "paper_id"}

    def test_vector_arm_uses_day3s_id_column(self):
        sql = probe.VECTOR_SQL.format(**self.render())
        self.assertIn("e.paper_id AS arxiv_id", sql)
        self.assertNotIn("{", sql)

    def keyword_sql(self, mode="or"):
        return probe.KEYWORD_SQL.format(
            table="paper_embeddings", meta="paper_meta", idcol="paper_id",
            query_expr=probe.QUERY_EXPR[mode],
        )

    def test_keyword_arm_filters_categories_the_same_way_as_the_api(self):
        sql = self.keyword_sql()
        self.assertIn("string_to_array(coalesce(m.categories, ''), ' ')", sql)
        self.assertNotIn("{", sql)

    def test_query_text_is_always_bound_never_interpolated(self):
        # both modes must take the user's words as a parameter
        self.assertIn("%(q)s", self.keyword_sql("and"))
        self.assertIn("%(tsq)s", self.keyword_sql("or"))
        for mode in ("and", "or"):
            self.assertNotIn("+ query", self.keyword_sql(mode))

    def test_and_mode_matches_the_original_semantics(self):
        self.assertIn("websearch_to_tsquery", self.keyword_sql("and"))
        self.assertNotIn("to_tsquery('english', %(tsq)s)", self.keyword_sql("and"))

    def test_or_mode_uses_the_ored_tsquery(self):
        self.assertIn("to_tsquery('english', %(tsq)s)", self.keyword_sql("or"))

    def test_titles_sql_takes_an_array(self):
        self.assertIn("ANY(%(ids)s)", probe.TITLES_SQL.format(**self.render()))


class TsqueryTests(unittest.TestCase):
    def test_terms_are_ored_not_anded(self):
        # this is the whole point: websearch_to_tsquery ANDs, which returned 1 of
        # 4,271 papers for a five-word query
        self.assertEqual(probe.tsquery_or("BERT pretraining masked language model"),
                         "bert | pretraining | masked | language | model")

    def test_names_survive(self):
        self.assertEqual(probe.tsquery_or("Yann LeCun"), "yann | lecun")

    def test_punctuation_and_case_are_normalised(self):
        self.assertEqual(probe.tsquery_or("  Sparse  Attention!! "), "sparse | attention")

    def test_duplicate_terms_collapse(self):
        self.assertEqual(probe.tsquery_or("model model model"), "model")

    def test_single_letters_are_dropped(self):
        self.assertEqual(probe.tsquery_or("a b model"), "model")

    def test_empty_input_is_empty(self):
        self.assertEqual(probe.tsquery_or(""), "")
        self.assertEqual(probe.tsquery_or(None), "")

    def test_sql_has_a_builder_per_mode(self):
        self.assertEqual(set(probe.QUERY_EXPR), {"and", "or"})
        self.assertIn("websearch_to_tsquery", probe.QUERY_EXPR["and"])
        self.assertIn("to_tsquery", probe.QUERY_EXPR["or"])


class LiteralTests(unittest.TestCase):
    def test_vector_literal_matches_the_api(self):
        from docpulse.search.db import vector_literal as api_literal

        values = [0.1, -0.25, 0.3333333]
        self.assertEqual(probe.vector_literal(values), api_literal(values))


if __name__ == "__main__":
    unittest.main()
