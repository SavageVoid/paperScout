"""Day 3: the adapters onto Day 1/2 code. These are the tests that would catch a
column rename in silver before a real run does."""

from __future__ import annotations

import json
import unittest

from _day3_fakes import FakeStore, make_silver_rows, seed_silver_store
from docpulse.embed import compat


class NormaliseRowTests(unittest.TestCase):
    def test_id_aliases(self):
        for alias in ("paper_id", "id", "arxiv_id", "id_full"):
            out = compat.normalise_row({alias: "2601.00001", "title": "t", "abstract": "a"})
            self.assertEqual(out["paper_id"], "2601.00001", alias)

    def test_missing_id_returns_none(self):
        self.assertIsNone(compat.normalise_row({"title": "t", "abstract": "a"}))

    def test_version_defaults_to_1(self):
        self.assertEqual(compat.normalise_row({"paper_id": "x"})["version"], 1)

    def test_version_string_is_coerced(self):
        self.assertEqual(compat.normalise_row({"paper_id": "x", "version": "3"})["version"], 3)

    def test_version_garbage_falls_back_to_1(self):
        self.assertEqual(compat.normalise_row({"paper_id": "x", "version": "v2"})["version"], 1)

    def test_text_hash_aliases(self):
        for alias in compat.HASH_KEYS:
            out = compat.normalise_row({"paper_id": "x", alias: "abc"})
            self.assertEqual(out["text_hash"], "abc", alias)

    def test_missing_text_hash_is_none(self):
        self.assertIsNone(compat.normalise_row({"paper_id": "x"})["text_hash"])

    def test_non_string_title_is_tolerated(self):
        out = compat.normalise_row({"paper_id": "x", "title": None, "abstract": 123})
        self.assertEqual(out["title"], "")
        self.assertEqual(out["abstract"], "")


class DecodeTests(unittest.TestCase):
    def test_as_rows_from_list(self):
        self.assertEqual(compat._as_rows([{"a": 1}]), [{"a": 1}])

    def test_as_rows_from_columns(self):
        self.assertEqual(
            compat._as_rows({"a": [1, 2], "b": ["x", "y"]}),
            [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}],
        )

    def test_as_rows_rejects_nonsense(self):
        with self.assertRaises(compat.CompatError):
            compat._as_rows(42)

    def test_jsonl_month(self):
        store = FakeStore({"k.jsonl": b'{"paper_id": "a"}\n{"paper_id": "b"}\n'})
        rows, mode = compat._decode_month(store, "k.jsonl")
        self.assertEqual(mode, "jsonl")
        self.assertEqual([r["paper_id"] for r in rows], ["a", "b"])

    def test_json_object_with_rows_key(self):
        store = FakeStore({"k.json": json.dumps({"rows": [{"paper_id": "a"}]}).encode()})
        rows, mode = compat._decode_month(store, "k.json")
        self.assertEqual(mode, "json")
        self.assertEqual(rows[0]["paper_id"], "a")

    def test_unknown_extension_is_a_clear_error(self):
        with self.assertRaises(compat.CompatError):
            compat._decode_month(FakeStore({"k.csv": b"a,b"}), "k.csv")


class ManifestLookupTests(unittest.TestCase):
    def test_returns_a_key_that_can_be_read(self):
        store = seed_silver_store()
        key = compat.find_silver_manifest_key(store)
        self.assertIn(key, store.objects)

    def test_missing_manifest_is_a_clear_error(self):
        with self.assertRaises(compat.CompatError) as ctx:
            compat.find_silver_manifest_key(FakeStore({}))
        self.assertIn("make silver", str(ctx.exception))

    def test_imported_key_that_is_absent_from_the_store_falls_back_to_a_scan(self):
        """Regression: on the real repo MANIFEST_KEY *is* importable.

        Trusting the import without checking the object made this raise a bare
        KeyError later, instead of finding the manifest that is actually there.
        """
        key = "silver/other/_manifest.json"
        store = FakeStore({key: json.dumps({"months": []}).encode()})
        found = compat.find_silver_manifest_key(store)
        self.assertEqual(found, key)
        self.assertIn(found, store.objects)


class LoadSilverTests(unittest.TestCase):
    def test_end_to_end_from_a_seeded_store(self):
        store = seed_silver_store()
        snap = compat.load_silver(store)
        self.assertEqual(snap.n_rows, 5)
        self.assertEqual(snap.decode_mode, "json")
        self.assertEqual(snap.manifest["n_papers_total"], 5)
        ids = sorted(r["paper_id"] for r in snap.rows)
        self.assertEqual(ids, [f"2601.0000{i}" for i in range(5)])
        self.assertEqual(snap.rows[0]["text_hash"], make_silver_rows()[0]["text_hash"])

    def test_without_text_hash_the_column_is_none(self):
        store = seed_silver_store(with_text_hash=False)
        snap = compat.load_silver(store)
        self.assertTrue(all(r["text_hash"] is None for r in snap.rows))

    def test_summary_is_json_safe(self):
        snap = compat.load_silver(seed_silver_store())
        json.dumps(snap.summary())


if __name__ == "__main__":
    unittest.main()
