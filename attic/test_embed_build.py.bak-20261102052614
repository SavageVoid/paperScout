"""Day 3: build behaviour, including the crash-safety and model-guard guarantees."""

from __future__ import annotations

import json
import unittest

from _day3_fakes import FakeDb, FakeStore, FlakyEncoder, loader, make_snapshot, make_silver_rows
from docpulse.embed.build import LockBusy, ModelChangeError, run
from docpulse.embed.config import EmbeddingSettings
from docpulse.embed.encoder import HashEncoder


def settings(**kw):
    base = dict(
        dsn="postgresql://u@h/db",
        table="paper_embeddings",
        batch_size=2,
        max_chars=4000,
        manifest_key="gold/embeddings/_manifest.json",
    )
    base.update(kw)
    return EmbeddingSettings(**base)


def encoder(dim=16):
    return HashEncoder(dim=dim)


class BuildBasicsTests(unittest.TestCase):
    def test_dry_run_writes_nothing(self):
        store, db = FakeStore(), FakeDb()
        out = run(
            store=store,
            db=db,
            encoder=encoder(),
            settings=settings(),
            silver_loader=loader(make_snapshot()),
            dry_run=True,
        )
        self.assertEqual(out["status"], "dry_run")
        self.assertEqual(out["plan"]["n_to_embed"], 5)
        self.assertEqual(store.puts, [])          # no manifest
        self.assertEqual(db.batches, [])          # no writes
        self.assertEqual(db.runs, {})             # no ledger entry

    def test_happy_path_embeds_everything_in_batches(self):
        store, db = FakeStore(), FakeDb()
        out = run(
            store=store,
            db=db,
            encoder=encoder(),
            settings=settings(),
            silver_loader=loader(make_snapshot()),
        )
        self.assertEqual(out["status"], "success")
        self.assertEqual(out["n_embedded"], 5)
        self.assertEqual(out["n_failed"], 0)
        self.assertEqual(db.batches, [2, 2, 1])   # batch_size=2
        self.assertEqual(len(db.rows), 5)
        self.assertEqual(len(db.models), 1)

    def test_manifest_is_written_last_and_is_valid_json(self):
        store, db = FakeStore(), FakeDb()
        run(
            store=store,
            db=db,
            encoder=encoder(),
            settings=settings(),
            silver_loader=loader(make_snapshot()),
        )
        self.assertEqual(store.puts, ["gold/embeddings/_manifest.json"])
        manifest = store.json("gold/embeddings/_manifest.json")
        self.assertEqual(manifest["status"], "success")
        self.assertEqual(manifest["n_embedded"], 5)
        self.assertIn("model", manifest)
        self.assertEqual(manifest["model"]["dim"], 16)

    def test_ledger_records_the_run(self):
        store, db = FakeStore(), FakeDb()
        out = run(
            store=store,
            db=db,
            encoder=encoder(),
            settings=settings(),
            silver_loader=loader(make_snapshot()),
        )
        ledger = db.runs[out["run_id"]]
        self.assertEqual(ledger["status"], "success")
        self.assertEqual(ledger["n_embedded"], 5)
        self.assertEqual(ledger["n_planned"], 5)

    def test_limit_caps_targets(self):
        store, db = FakeStore(), FakeDb()
        out = run(
            store=store,
            db=db,
            encoder=encoder(),
            settings=settings(),
            silver_loader=loader(make_snapshot()),
            limit=3,
        )
        self.assertEqual(out["n_embedded"], 3)
        self.assertEqual(len(db.rows), 3)

    def test_no_targets_still_writes_a_manifest(self):
        store, db = FakeStore(), FakeDb()
        run(
            store=store,
            db=db,
            encoder=encoder(),
            settings=settings(),
            silver_loader=loader(make_snapshot()),
        )
        store.puts.clear()
        out = run(
            store=store,
            db=db,
            encoder=encoder(),
            settings=settings(),
            silver_loader=loader(make_snapshot()),
        )
        self.assertEqual(out["n_embedded"], 0)
        self.assertEqual(out["plan"]["n_unchanged"], 5)
        self.assertEqual(store.puts, ["gold/embeddings/_manifest.json"])


class IncrementalTests(unittest.TestCase):
    def test_second_run_embeds_nothing(self):
        store, db = FakeStore(), FakeDb()
        kwargs = dict(
            store=store, db=db, encoder=encoder(), settings=settings(),
            silver_loader=loader(make_snapshot()),
        )
        run(**kwargs)
        out = run(**kwargs)
        self.assertEqual(out["n_embedded"], 0)
        self.assertEqual(out["plan"]["n_unchanged"], 5)
        self.assertEqual(len(db.rows), 5)

    def test_changed_text_is_re_embedded(self):
        store, db = FakeStore(), FakeDb()
        run(
            store=store, db=db, encoder=encoder(), settings=settings(),
            silver_loader=loader(make_snapshot()),
        )
        rows = make_silver_rows(5)
        rows[0]["text_hash"] = "touched"
        out = run(
            store=store, db=db, encoder=encoder(), settings=settings(),
            silver_loader=loader(make_snapshot(rows)),
        )
        self.assertEqual(out["n_embedded"], 1)
        self.assertEqual(out["plan"]["n_changed"], 1)
        self.assertEqual(db.rows[rows[0]["paper_id"]][1], "touched")


class CrashSafetyTests(unittest.TestCase):
    def test_failure_mid_run_keeps_committed_batches_and_rerun_resumes(self):
        store, db = FakeStore(), FakeDb()
        flaky = FlakyEncoder(encoder(), fail_on_call=2)
        with self.assertRaises(RuntimeError):
            run(
                store=store, db=db, encoder=flaky, settings=settings(),
                silver_loader=loader(make_snapshot()),
            )
        # first batch (2 papers) committed, second failed
        self.assertEqual(len(db.rows), 2)
        self.assertEqual(db.batches, [2])
        failed_run = list(db.runs.values())[-1]
        self.assertEqual(failed_run["status"], "failed")
        self.assertEqual(failed_run["n_embedded"], 2)
        self.assertEqual(failed_run["n_failed"], 3)
        self.assertIn("simulated worker death", failed_run["notes"]["error"])
        # no manifest: the layer is not advertised as complete
        self.assertEqual(store.puts, [])

        # rerun resumes: only the remaining three are planned
        out = run(
            store=store, db=db, encoder=encoder(), settings=settings(),
            silver_loader=loader(make_snapshot()),
        )
        self.assertEqual(out["plan"]["n_unchanged"], 2)
        self.assertEqual(out["n_embedded"], 3)
        self.assertEqual(len(db.rows), 5)

    def test_busy_lock_refuses_to_run(self):
        db = FakeDb(lock_available=False)
        with self.assertRaises(LockBusy):
            run(
                store=FakeStore(), db=db, encoder=encoder(), settings=settings(),
                silver_loader=loader(make_snapshot()),
            )
        self.assertEqual(db.rows, {})


class ModelGuardTests(unittest.TestCase):
    def _seeded_db(self):
        db = FakeDb()
        other = encoder(dim=8)
        db.ensure_model(other.info())
        db.upsert_batch(
            [(r["paper_id"], 1, r["text_hash"], other.fingerprint, [1.0] * 8, 10)
             for r in make_silver_rows(2)]
        )
        return db

    def test_model_change_is_refused(self):
        db = self._seeded_db()
        with self.assertRaises(ModelChangeError) as ctx:
            run(
                store=FakeStore(), db=db, encoder=encoder(), settings=settings(),
                silver_loader=loader(make_snapshot()),
            )
        self.assertIn("vector spaces", str(ctx.exception))
        # nothing was written
        self.assertEqual(len(db.rows), 2)

    def test_model_change_allowed_when_asked(self):
        db = self._seeded_db()
        out = run(
            store=FakeStore(), db=db, encoder=encoder(),
            settings=settings(allow_model_change=True),
            silver_loader=loader(make_snapshot()),
        )
        self.assertEqual(out["status"], "success")
        self.assertEqual(out["n_dropped_for_model_change"], 2)
        self.assertEqual(len(db.models), 2)  # model registry keeps history
        self.assertEqual({v[2] for v in db.rows.values()}, {encoder().fingerprint})

    def test_rebuild_drops_and_reembeds(self):
        store, db = FakeStore(), FakeDb()
        kwargs = dict(
            store=store, db=db, encoder=encoder(), settings=settings(),
            silver_loader=loader(make_snapshot()),
        )
        run(**kwargs)
        out = run(rebuild=True, **kwargs)
        self.assertEqual(out["n_dropped_for_rebuild"], 5)
        self.assertEqual(out["n_embedded"], 5)
        self.assertEqual(len(db.rows), 5)


class OrphanTests(unittest.TestCase):
    def test_orphans_are_pruned_only_when_asked(self):
        store, db = FakeStore(), FakeDb()
        run(
            store=store, db=db, encoder=encoder(), settings=settings(),
            silver_loader=loader(make_snapshot()),
        )
        db.upsert_batch([("9999.99999", 1, "h", encoder().fingerprint, [1.0] * 16, 5)])
        # default: keep
        out = run(
            store=store, db=db, encoder=encoder(), settings=settings(),
            silver_loader=loader(make_snapshot()),
        )
        self.assertEqual(out["n_orphans_pruned"], 0)
        self.assertIn("9999.99999", db.rows)
        # opt in: prune
        out = run(
            store=store, db=db, encoder=encoder(), settings=settings(prune_orphans=True),
            silver_loader=loader(make_snapshot()),
        )
        self.assertEqual(out["n_orphans_pruned"], 1)
        self.assertNotIn("9999.99999", db.rows)


if __name__ == "__main__":
    unittest.main()
