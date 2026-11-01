"""Day 3: planning decides what to embed. Pure functions, no I/O."""

from __future__ import annotations

import unittest

from docpulse.embed.plan import (
    build_plan,
    compose_text,
    computed_text_hash,
    dedupe_rows,
    truncate,
)

FP = "model-fp"


def row(pid="2601.00001", version=1, text_hash="h1", title="T", abstract="A"):
    return {
        "paper_id": pid,
        "version": version,
        "text_hash": text_hash,
        "title": title,
        "abstract": abstract,
    }


class ComposeTextTests(unittest.TestCase):
    def test_title_and_abstract(self):
        self.assertEqual(compose_text("Title", "Body"), "Title\n\nBody")

    def test_whitespace_is_stripped(self):
        self.assertEqual(compose_text("  T \n", "\n A  "), "T\n\nA")

    def test_missing_parts(self):
        self.assertEqual(compose_text("", "Body"), "Body")
        self.assertEqual(compose_text("Title", ""), "Title")

    def test_truncate(self):
        self.assertEqual(truncate("abcdef", 3), "abc")
        self.assertEqual(truncate("abc", 10), "abc")
        self.assertEqual(len(truncate("a" * 100, 10)), 10)


class DedupeTests(unittest.TestCase):
    def test_highest_version_wins(self):
        from docpulse.embed.plan import Plan

        plan = Plan()
        out = dedupe_rows([row(version=1, text_hash="old"), row(version=3, text_hash="new")], plan)
        self.assertEqual(out["2601.00001"]["text_hash"], "new")
        self.assertEqual(plan.n_duplicate_ids_collapsed, 1)
        self.assertEqual(plan.n_unique_papers, 1)

    def test_lower_version_does_not_win(self):
        from docpulse.embed.plan import Plan

        plan = Plan()
        out = dedupe_rows([row(version=3, text_hash="new"), row(version=1, text_hash="old")], plan)
        self.assertEqual(out["2601.00001"]["text_hash"], "new")

    def test_rows_without_id_are_counted_not_crashed(self):
        from docpulse.embed.plan import Plan

        plan = Plan()
        bad = row()
        bad["paper_id"] = ""
        out = dedupe_rows([bad, row(pid="ok")], plan)
        self.assertEqual(set(out), {"ok"})
        self.assertEqual(plan.n_missing_id, 1)


class BuildPlanTests(unittest.TestCase):
    def test_everything_new(self):
        plan = build_plan([row(pid="a"), row(pid="b")], {}, FP)
        self.assertEqual(plan.n_new, 2)
        self.assertEqual(plan.n_to_embed, 2)
        self.assertEqual([t.paper_id for t in plan.targets], ["a", "b"])  # sorted

    def test_unchanged_is_skipped(self):
        plan = build_plan([row(pid="a", text_hash="h1")], {"a": ("h1", FP)}, FP)
        self.assertEqual(plan.n_unchanged, 1)
        self.assertEqual(plan.n_to_embed, 0)

    def test_changed_text_is_embedded(self):
        plan = build_plan([row(pid="a", text_hash="h2")], {"a": ("h1", FP)}, FP)
        self.assertEqual(plan.n_changed, 1)
        self.assertEqual(plan.n_to_embed, 1)

    def test_model_mismatch_is_embedded_and_counted(self):
        plan = build_plan([row(pid="a", text_hash="h1")], {"a": ("h1", "other-fp")}, FP)
        self.assertEqual(plan.n_model_mismatch, 1)
        self.assertEqual(plan.n_to_embed, 1)

    def test_missing_text_is_not_embedded(self):
        plan = build_plan([row(pid="a", title="", abstract="")], {}, FP)
        self.assertEqual(plan.n_missing_text, 1)
        self.assertEqual(plan.n_to_embed, 0)

    def test_text_hash_is_computed_when_absent_and_flagged(self):
        r = row(pid="a")
        r["text_hash"] = None
        plan = build_plan([r], {}, FP)
        self.assertEqual(plan.n_to_embed, 1)
        self.assertEqual(plan.text_hash_computed, 1)
        expected = computed_text_hash(compose_text("T", "A"))
        self.assertEqual(plan.targets[0].text_hash, expected)

    def test_char_count_and_truncation(self):
        r = row(pid="a", title="t" * 50, abstract="a" * 50)
        plan = build_plan([r], {}, FP, max_chars=20)
        self.assertEqual(plan.targets[0].chars, 20)
        self.assertEqual(len(plan.targets[0].text), 20)

    def test_chars_to_embed_sums_targets_only(self):
        plan = build_plan(
            [row(pid="a", text_hash="h1"), row(pid="b", text_hash="h2")],
            {"a": ("h1", FP)},
            FP,
        )
        self.assertEqual(plan.n_to_embed, 1)
        self.assertEqual(plan.chars_to_embed, len(plan.targets[0].text))

    def test_summary_is_reportable(self):
        summary = build_plan([row(pid="a")], {}, FP).summary()
        for key in ("n_new", "n_changed", "n_unchanged", "n_to_embed", "chars_to_embed"):
            self.assertIn(key, summary)


if __name__ == "__main__":
    unittest.main()
