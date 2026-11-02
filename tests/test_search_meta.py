"""Day 4: building ``paper_meta`` rows from silver.

Written after a real bug: pyarrow hands ``categories`` over as a Python list, and
``str()`` stored it as the literal text "['cs.CL', 'cs.CV']". The search filter
splits on spaces, so ``?category=cs.CL`` matched nothing — silently, with an empty
result list rather than an error.

The script is loaded by path because it is a CLI, not an importable module; the
functions it exposes are pure, which is the point.
"""

from __future__ import annotations

import importlib.util
import pathlib
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "day4_load_meta.py"

_spec = importlib.util.spec_from_file_location("day4_load_meta_under_test", SCRIPT)
assert _spec and _spec.loader
meta = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(meta)

ID_KEYS = ("arxiv_id", "paper_id", "id")
VERSION_KEYS = ("version",)
HASH_KEYS = ("text_hash",)


def build(raw: list[dict]) -> list[dict]:
    return meta.build_rows(
        raw, id_keys=ID_KEYS, version_keys=VERSION_KEYS, hash_keys=HASH_KEYS
    )


class CategoryTests(unittest.TestCase):
    def test_a_python_list_becomes_tokens(self):
        self.assertEqual(meta.normalise_categories(["cs.CL", "cs.CV"]), "cs.CL cs.CV")

    def test_a_stringified_list_becomes_tokens(self):
        self.assertEqual(meta.normalise_categories("['cs.LG', 'cs.AI']"), "cs.LG cs.AI")

    def test_already_clean_text_is_unchanged(self):
        self.assertEqual(meta.normalise_categories("cs.CL cs.CV"), "cs.CL cs.CV")

    def test_comma_separated(self):
        self.assertEqual(meta.normalise_categories("cs.LG,cs.AI"), "cs.LG cs.AI")

    def test_duplicates_collapse(self):
        self.assertEqual(meta.normalise_categories(["cs.CL", "cs.CL"]), "cs.CL")

    def test_empty_values_are_none(self):
        for value in (None, "", "   ", [], "[]"):
            self.assertIsNone(meta.normalise_categories(value))

    def test_single_category_survives_the_filter_shape(self):
        # this is what a category filter joins against: split(" ") on the result
        self.assertEqual(meta.normalise_categories(["astro-ph.CO"]).split(" "), ["astro-ph.CO"])


class BuildRowsTests(unittest.TestCase):
    def _raw(self, **kw):
        base = {
            "arxiv_id": "2601.00001",
            "version": 1,
            "title": "T",
            "abstract": "A",
            "categories": ["cs.LG"],
            "primary_category": "cs.LG",
            "published": "2026-01-02T00:00:00Z",
            "updated": "2026-01-02T00:00:00Z",
            "text_hash": "abc",
        }
        base.update(kw)
        return base

    def test_maps_fields(self):
        row = build([self._raw()])[0]
        self.assertEqual(row["arxiv_id"], "2601.00001")
        self.assertEqual(row["categories"], "cs.LG")
        self.assertEqual(row["published"], "2026-01-02T00:00:00Z")
        self.assertEqual(row["version"], 1)

    def test_highest_version_wins(self):
        rows = build([self._raw(version=1, title="old"), self._raw(version=3, title="new")])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "new")

    def test_rows_without_an_id_are_dropped(self):
        self.assertEqual(build([self._raw(arxiv_id=None)]), [])

    def test_bad_version_falls_back_to_one(self):
        row = build([self._raw(version="not-a-number")])[0]
        self.assertEqual(row["version"], 1)

    def test_output_is_sorted_and_unique(self):
        rows = build([self._raw(arxiv_id="b"), self._raw(arxiv_id="a")])
        self.assertEqual([r["arxiv_id"] for r in rows], ["a", "b"])


if __name__ == "__main__":
    unittest.main()
