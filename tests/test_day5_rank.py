"""Day 5 step 2: the ranking probe's own logic.

The numbers this probe prints decide whether Day 5 ships fusion, so its token
handling and pruning are tested rather than trusted. The SQL is validated by
running it against the real corpus; what is testable offline is the rendering and
the pieces that turn a histogram into a verdict.
"""

from __future__ import annotations

import importlib.util
import pathlib
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "day5_rank_probe.py"
_spec = importlib.util.spec_from_file_location("day5_rank_probe_under_test", SCRIPT)
assert _spec and _spec.loader
probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe)


class TokenizeTests(unittest.TestCase):
    def test_stopwords_are_dropped(self):
        # 'for' appears in 4,176 of 4,271 papers; it must never reach a tsquery
        self.assertEqual(probe.tokenize("sparse attention for long documents"),
                         ["sparse", "attention", "long", "documents"])

    def test_symbols_allowed_inside_tokens_are_lowercased(self):
        self.assertEqual(probe.tokenize("BERT-4 and GPT4"), ["bert", "gpt4"])

    def test_duplicates_collapse_but_order_is_kept(self):
        self.assertEqual(probe.tokenize("model language model"), ["model", "language"])

    def test_single_letters_dropped(self):
        self.assertEqual(probe.tokenize("a b transformer"), ["transformer"])

    def test_nothing_but_stopwords_is_empty(self):
        self.assertEqual(probe.tokenize("the of and"), [])

    def test_empty_input(self):
        self.assertEqual(probe.tokenize(""), [])
        self.assertEqual(probe.tokenize(None), [])


class AnchorTests(unittest.TestCase):
    def test_rarest_tokens_are_the_anchors(self):
        tokens = ["model", "language", "bert", "masked"]
        df = {"model": 3272, "language": 812, "bert": 66, "masked": 75}
        self.assertEqual(probe.pick_anchors(tokens, df), ["bert", "masked"])

    def test_absent_tokens_are_not_anchors(self):
        # 'yann' has df 0, so it can never appear in a hit and must not score
        tokens = ["yann", "lecun"]
        self.assertEqual(probe.pick_anchors(tokens, {"yann": 0, "lecun": 0}), [])

    def test_partly_absent_query_keeps_the_present_token(self):
        tokens = ["yann", "transformer"]
        self.assertEqual(probe.pick_anchors(tokens, {"yann": 0, "transformer": 900}),
                         ["transformer"])

    def test_ties_resolve_by_query_order(self):
        tokens = ["alpha", "beta"]
        self.assertEqual(probe.pick_anchors(tokens, {"alpha": 5, "beta": 5}), ["alpha", "beta"])


class PruneTests(unittest.TestCase):
    def tokens(self):
        return ["bert", "pretraining", "masked", "language", "model"]

    def df(self):
        return {"bert": 66, "pretraining": 146, "masked": 75, "language": 812, "model": 3272}

    def test_high_df_tokens_are_dropped(self):
        kept = probe.prune(self.tokens(), self.df(), total=4271, max_ratio=0.10)
        self.assertEqual(kept, ["bert", "pretraining", "masked"])

    def test_order_follows_the_query(self):
        kept = probe.prune(self.tokens(), self.df(), total=4271)
        self.assertEqual(kept, [t for t in self.tokens() if t in kept])

    def test_never_prunes_below_the_minimum(self):
        # nothing survives a 10% threshold here, so the two *rarest* are restored
        tokens = ["model", "language", "network"]
        df = {"model": 3272, "language": 812, "network": 600}
        kept = probe.prune(tokens, df, total=4271, max_ratio=0.10)
        self.assertEqual(len(kept), probe.KEEP_MIN)
        self.assertEqual(kept, ["language", "network"])
        self.assertNotIn("model", kept)

    def test_absent_tokens_are_kept_because_they_discriminate(self):
        tokens = ["bert", "yann"]
        kept = probe.prune(tokens, {"bert": 66, "yann": 0}, total=4271)
        self.assertEqual(kept, ["bert", "yann"])

    def test_single_token_query_survives(self):
        self.assertEqual(probe.prune(["model"], {"model": 3272}, total=4271), ["model"])


class CoverageTests(unittest.TestCase):
    def test_full_and_near_miss_counts(self):
        cov = probe.coverage({5: 0, 4: 3, 3: 41, 1: 900}, n_tokens=5)
        self.assertEqual(cov["full"], 0)
        self.assertEqual(cov["near"], 3)
        self.assertEqual(cov["any"], 944)

    def test_missing_buckets_are_zero_not_keyerror(self):
        cov = probe.coverage({}, n_tokens=3)
        self.assertEqual(cov, {"full": 0, "near": 0, "any": 0})


class HitRateTests(unittest.TestCase):
    def test_counts_only_the_top_k(self):
        ids = ["a", "b", "c", "d", "e", "f"]
        hits = {"a": 1, "c": 0, "f": 2}
        self.assertEqual(probe.hit_rate(ids, hits, k=5), 1)
        self.assertEqual(probe.hit_rate(ids, hits, k=6), 2)

    def test_empty_inputs(self):
        self.assertEqual(probe.hit_rate([], {}), 0)


class SqlRenderTests(unittest.TestCase):
    def test_no_unfilled_brace_placeholders(self):
        # f-strings, not str.format: a stray {name} would reach Postgres verbatim
        for _name, (tsv, rank) in probe.variants().items():
            sql = probe.ranked_sql(tsv, rank)
            self.assertNotIn("{", sql.replace("'{0.05,0.2,0.3,1.0}'", ""))
        for sql in (probe.df_sql(), probe.coverage_sql(), probe.anchor_sql()):
            self.assertNotIn("{", sql)

    def test_query_text_is_bound_not_interpolated(self):
        for _name, (tsv, rank) in probe.variants().items():
            self.assertIn("%(tsq)s", probe.ranked_sql(tsv, rank))

    def test_weighted_arm_weights_the_title_higher_than_the_abstract(self):
        self.assertIn("setweight(to_tsvector('english', coalesce(m.title, '')), 'A')",
                      probe.TSV_W)
        self.assertIn("setweight(to_tsvector('english', coalesce(m.abstract, '')), 'B')",
                      probe.TSV_W)
        self.assertIn("'{0.05,0.2,0.3,1.0}'", probe.WEIGHTS)

    def test_plain_arm_does_not_weight(self):
        tsv, _rank = probe.variants()["plain"]
        self.assertNotIn("setweight", tsv)

    def test_df_sql_scores_every_token_in_one_scan(self):
        sql = probe.df_sql()
        self.assertIn("unnest(%(toks)s::text[])", sql)
        self.assertIn("GROUP BY t.tok", sql)
        self.assertEqual(sql.count("to_tsvector"), 1)

    def test_coverage_sql_counts_tokens_per_paper(self):
        sql = probe.coverage_sql()
        self.assertIn("count(*) AS n", sql)
        self.assertIn("GROUP BY arxiv_id", sql)

    def test_anchor_sql_is_restricted_to_the_candidates(self):
        self.assertIn("ANY(%(ids)s::text[])", probe.anchor_sql())

    def test_vector_arm_uses_day3s_id_column(self):
        self.assertIn("e.paper_id AS arxiv_id", probe.VECTOR_SQL)

    def test_explain_covers_everything_without_a_database(self):
        probe.print_explain()  # must not raise, must not connect


class LiteralTests(unittest.TestCase):
    def test_vector_literal_matches_the_api(self):
        try:
            from docpulse.search.db import vector_literal as api_literal
        except ModuleNotFoundError:  # probe tested outside the container
            self.skipTest("docpulse.search.db not importable here")

        values = [0.1, -0.25, 0.3333333]
        self.assertEqual(probe.vector_literal(values), api_literal(values))


if __name__ == "__main__":
    unittest.main()
