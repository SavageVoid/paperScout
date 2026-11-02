"""Day 3: the text_hash audit must recover a recipe it is given, and must not
invent one. (item B4)"""

from __future__ import annotations

import hashlib
import unittest

from docpulse.embed.hashaudit import candidates, describe, identify


def _rows(n: int = 60) -> list[dict]:
    rows = []
    for i in range(n):
        name = f"paper number {i}"
        rows.append(
            {
                "arxiv_id": f"2401.{i:05d}",
                "version": 1 + i % 3,
                "title": f"A Study of {name}",
                "abstract": f"We examine {name} in depth and report results.",
                "categories": "cs.LG cs.AI",
                "primary_category": "cs.LG",
                "authors": "A. Author, B. Author",
                "published": "2024-01-01",
                "text_hash": None,
            }
        )
    return rows


def _fill(rows: list[dict], func) -> list[dict]:
    for row in rows:
        row["text_hash"] = func(row)
    return rows


class CandidateSpaceTests(unittest.TestCase):
    def test_candidates_is_large_but_finite(self):
        specs = list(candidates())
        self.assertGreater(len(specs), 1000)
        self.assertLess(len(specs), 20000)
        # deterministic order, so two runs explain the same finding
        self.assertEqual(describe(specs[0]), describe(list(candidates())[0]))

    def test_describe_names_the_fields(self):
        spec = next(s for s in candidates() if s["fields"] == ("title", "abstract"))
        text = describe(spec)
        self.assertIn("title", text)
        self.assertIn("abstract", text)


class IdentifyTests(unittest.TestCase):
    def test_recovers_title_and_abstract_recipe(self):
        rows = _fill(
            _rows(),
            lambda r: hashlib.sha256(f"{r['title']}\n\n{r['abstract']}".encode()).hexdigest(),
        )
        report = identify(rows, sample=20)
        self.assertEqual(report["verdict"], "identified")
        self.assertTrue(report["covers_title"])
        self.assertIn("sha256", report["recipe"])
        self.assertNotIn("warning", report)
        self.assertEqual(report["hash_length"], [64])

    def test_abstract_only_hash_is_flagged_as_b4(self):
        # this is the failure mode that would silently freeze stale vectors
        rows = _fill(_rows(), lambda r: hashlib.sha256(r["abstract"].encode()).hexdigest())
        report = identify(rows, sample=20)
        self.assertEqual(report["verdict"], "identified")
        self.assertFalse(report["covers_title"])
        self.assertIn("B4 CONFIRMED", report["warning"])
        # two papers with the same abstract but different titles share a hash
        rows[1]["abstract"] = rows[0]["abstract"]
        rows[1]["text_hash"] = rows[0]["text_hash"]
        report = identify(rows, sample=20)
        self.assertGreaterEqual(report["n_identical_hashes_different_text"], 1)

    def test_unknown_recipe_is_not_invented(self):
        rows = _fill(_rows(), lambda r: hashlib.sha256(b"something else entirely").hexdigest())
        report = identify(rows, sample=20)
        self.assertEqual(report["verdict"], "not_identified")
        self.assertIn("build.py", report["hint"])

    def test_rows_without_hash_are_ignored(self):
        rows = _fill(
            _rows(),
            lambda r: hashlib.sha256(f"{r['title']}\n\n{r['abstract']}".encode()).hexdigest(),
        )
        rows[0]["text_hash"] = None
        rows[1]["text_hash"] = ""
        report = identify(rows, sample=20)
        self.assertEqual(report["n_rows_usable"], len(rows) - 2)
        self.assertEqual(report["verdict"], "identified")

    def test_empty_input_does_not_raise(self):
        report = identify([], sample=10)
        self.assertEqual(report["verdict"], "no_rows_with_text_hash")


if __name__ == "__main__":
    unittest.main()
