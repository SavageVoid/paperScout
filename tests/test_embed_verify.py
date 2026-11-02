"""Day 3: independent verification catches the failures build would hide."""

from __future__ import annotations

import unittest

from _day3_fakes import FakeDb, FakeStore, loader, make_silver_rows, make_snapshot
from docpulse.embed.config import EmbeddingSettings
from docpulse.embed.encoder import HashEncoder
from docpulse.embed.verify import run as verify_run


def settings(**kw):
    base = dict(dsn="postgresql://u@h/db", table="paper_embeddings")
    base.update(kw)
    return EmbeddingSettings(**base)


def encoder(dim=16):
    return HashEncoder(dim=dim)


def unit(dim: int) -> list[float]:
    """A correctly normalised vector, so the fixture is not what fails V3."""
    return [1.0 / (dim ** 0.5)] * dim


def seeded_db(rows=None, dim=16):
    rows = make_silver_rows() if rows is None else rows
    enc = HashEncoder(dim=dim)
    db = FakeDb()
    db.ensure_model(enc.info())
    db.upsert_batch(
        [(r["paper_id"], r["version"], r["text_hash"], enc.fingerprint, unit(dim), 20) for r in rows]
    )
    return db, enc


class VerifyTests(unittest.TestCase):
    def _verify(self, db, enc, rows=None, **kw):
        return verify_run(
            store=FakeStore(),
            db=db,
            encoder=enc,
            settings=settings(),
            silver_loader=loader(make_snapshot(make_silver_rows() if rows is None else rows)),
            **kw,
        )

    def test_clean_layer_passes(self):
        db, enc = seeded_db()
        out = self._verify(db, enc)
        self.assertTrue(out["ok"], out["errors"])
        self.assertEqual(out["n_rows"], 5)
        self.assertEqual(out["n_missing"], 0)
        self.assertEqual(out["n_stale_text_hash"], 0)
        self.assertTrue(out["deterministic"])

    def test_missing_vectors_fail_when_completeness_is_expected(self):
        db, enc = seeded_db(make_silver_rows(3))
        out = self._verify(db, enc)
        self.assertFalse(out["ok"])
        self.assertEqual(out["n_missing"], 2)
        self.assertTrue(any("V5" in e for e in out["errors"]))

    def test_missing_vectors_are_only_a_warning_when_allowed(self):
        db, enc = seeded_db(make_silver_rows(3))
        out = self._verify(db, enc, expect_complete=False)
        self.assertTrue(out["ok"], out["errors"])
        self.assertTrue(any("V5" in w for w in out["warnings"]))

    def test_stale_text_hash_is_an_error(self):
        db, enc = seeded_db()
        rows = make_silver_rows(5)
        rows[0]["text_hash"] = "changed-but-not-embedded"
        out = self._verify(db, enc, rows=rows)
        self.assertFalse(out["ok"])
        self.assertEqual(out["n_stale_text_hash"], 1)
        self.assertTrue(any("V7" in e for e in out["errors"]))

    def test_orphans_are_a_warning_not_a_failure(self):
        db, enc = seeded_db()
        db.upsert_batch([("9999.99999", 1, "h", enc.fingerprint, unit(16), 5)])
        out = self._verify(db, enc)
        self.assertTrue(out["ok"], out["errors"])
        self.assertEqual(out["n_orphans"], 1)
        self.assertTrue(any("V6" in w for w in out["warnings"]))

    def test_mixed_vector_spaces_are_an_error(self):
        db, enc = seeded_db()
        other = HashEncoder(dim=8)
        db.ensure_model(other.info())
        db.upsert_batch([("2601.99999", 1, "h", other.fingerprint, unit(8), 5)])
        out = self._verify(db, enc)
        self.assertFalse(out["ok"])
        self.assertTrue(any("V2" in e for e in out["errors"]))

    def test_wrong_dimension_is_an_error(self):
        db, enc = seeded_db()
        # a 32-d vector in a 16-d table
        db.rows["2601.88888"] = (1, "h", enc.fingerprint, [1.0] * 32, 5)
        out = self._verify(db, enc)
        self.assertFalse(out["ok"])
        self.assertTrue(any("V1" in e for e in out["errors"]))

    def test_unnormalised_vectors_are_an_error(self):
        db, enc = seeded_db()
        db.rows["2601.77777"] = (1, "h", enc.fingerprint, [3.0] * 16, 5)  # norm 12
        out = self._verify(db, enc)
        self.assertFalse(out["ok"])
        self.assertTrue(any("V3" in e for e in out["errors"]))

    # -- V9: silver's hash must be the hash of the text we embed -------------
    def test_hash_recipe_matches_by_default(self):
        db, enc = seeded_db()
        out = self._verify(db, enc)
        self.assertEqual(out["n_hash_mismatch"], 0)
        self.assertIn("compose_text", out["hash_recipe"])

    def test_silver_recipe_change_is_caught(self):
        # silver re-hashes with a different recipe (say it starts including the
        # categories) but the stored hashes are untouched: every vector now
        # describes text its hash no longer points at.
        rows = make_silver_rows()
        rows[2]["text_hash"] = "f" * 64
        db, enc = seeded_db(rows)
        out = self._verify(db, enc, rows=rows)
        self.assertEqual(out["n_hash_mismatch"], 1)
        self.assertFalse(out["ok"])
        self.assertTrue(any("V9" in e for e in out["errors"]))

    def test_hash_drift_can_be_downgraded(self):
        rows = make_silver_rows()
        rows[2]["text_hash"] = "f" * 64
        db, enc = seeded_db(rows)
        out = self._verify(db, enc, rows=rows, hash_drift_is_error=False)
        self.assertEqual(out["n_hash_mismatch"], 1)
        self.assertTrue(any("V9" in w for w in out["warnings"]))
        self.assertFalse(any("V9" in e for e in out["errors"]))

    def test_rows_without_a_silver_hash_are_skipped(self):
        rows = make_silver_rows()
        rows[1]["text_hash"] = None
        db, enc = seeded_db(rows)
        out = self._verify(db, enc, rows=rows)
        self.assertEqual(out["n_hash_mismatch"], 0)

    def test_empty_table_reports_zero_not_a_crash(self):
        db, enc = FakeDb(), HashEncoder(dim=16)
        db.ensure_model(enc.info())
        out = self._verify(db, enc)
        self.assertEqual(out["n_rows"], 0)
        self.assertFalse(out["ok"])  # everything is missing
        self.assertEqual(out["n_missing"], 5)


if __name__ == "__main__":
    unittest.main()
