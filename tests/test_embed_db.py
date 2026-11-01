"""Day 3: Postgres/pgvector behaviour.

Skipped unless DOCPULSE_TEST_PG_DSN points at a throwaway database, because the
tests create and drop real tables. In the embedder container:

  DOCPULSE_TEST_PG_DSN=$DOCPULSE_PG_DSN python -m pytest tests/test_embed_db.py
  # or simply: make embed-test-db
"""

from __future__ import annotations

import os
import unittest

DSN = os.environ.get("DOCPULSE_TEST_PG_DSN", "").strip()
_AUTO = not DSN and os.environ.get("DOCPULSE_TEST_DB", "").strip().lower() in {"1", "true", "yes"}
if _AUTO:
    # `make embed-test-db` sets DOCPULSE_TEST_DB=1 inside the embedder: derive the
    # DSN from the same settings the pipeline uses, but against a *_test database.
    # These tests DROP embedding_runs/embedding_models, so pointing them at the
    # live database would erase its run history.
    try:
        from psycopg import conninfo as _conninfo

        from docpulse.embed.config import dsn_from_env as _dsn_from_env

        _params = dict(_conninfo.conninfo_to_dict(_dsn_from_env(os.environ)))
        _params["dbname"] = f"{_params.get('dbname') or 'docpulse_embed'}_test"
        DSN = _conninfo.make_conninfo(**_params)
    except ImportError:
        # outside the embedder there is no driver: stay skipped rather than crash
        DSN = ""

_ENSURED = False


def _ensure_test_database(dsn: str) -> None:
    """Create the throwaway database and the vector extension on first use."""
    global _ENSURED
    if _ENSURED:
        return
    import psycopg
    from psycopg import conninfo

    params = conninfo.conninfo_to_dict(dsn)
    name = params.get("dbname") or "docpulse_embed_test"
    with psycopg.connect(
        conninfo.make_conninfo(**dict(params, dbname="postgres")), connect_timeout=10, autocommit=True
    ) as conn:
        with conn.cursor() as cur:
            cur.execute("select 1 from pg_database where datname = %s", (name,))
            if cur.fetchone() is None:
                cur.execute(f'create database "{name}"')
    with psycopg.connect(conninfo.make_conninfo(**params), connect_timeout=10, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("create extension if not exists vector")
    _ENSURED = True


@unittest.skipUnless(DSN, "set DOCPULSE_TEST_PG_DSN to run the database tests")
class PgEmbeddingDbTests(unittest.TestCase):
    table = "paper_embeddings_test"

    def setUp(self):
        _ensure_test_database(DSN)
        from docpulse.embed.db import PgEmbeddingDb

        self.db = PgEmbeddingDb(DSN, table=self.table)
        self.db.connect()
        with self.db.connect().cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {self.table}")
            cur.execute("DROP TABLE IF EXISTS embedding_runs")
            cur.execute("DROP TABLE IF EXISTS embedding_models")
        self.db.connect().commit()
        self.db.ensure_schema(8)

    def tearDown(self):
        try:
            with self.db.connect().cursor() as cur:
                cur.execute(f"DROP TABLE IF EXISTS {self.table}")
        finally:
            self.db.close()

    def info(self):
        from docpulse.embed.encoder import HashEncoder

        return HashEncoder(dim=8).info()

    def test_schema_is_idempotent(self):
        self.db.ensure_schema(8)
        self.db.ensure_schema(8)  # twice must not fail

    def test_upsert_and_read_back(self):
        info = self.info()
        self.db.ensure_model(info)
        fp = info["fingerprint"]
        self.db.upsert_batch([("a", 1, "h1", fp, [0.1] * 8, 10)])
        self.assertEqual(self.db.fetch_state()["a"], ("h1", fp))
        self.assertEqual(self.db.ids_for_model(fp), {"a"})

    def test_upsert_updates_existing_row(self):
        info = self.info()
        self.db.ensure_model(info)
        fp = info["fingerprint"]
        self.db.upsert_batch([("a", 1, "h1", fp, [0.1] * 8, 10)])
        self.db.upsert_batch([("a", 2, "h2", fp, [0.2] * 8, 12)])
        state = self.db.fetch_state()
        self.assertEqual(state["a"], ("h2", fp))
        self.assertEqual(self.db.stats(fp)["n_rows"], 1)

    def test_stats_see_normalised_vectors(self):
        info = self.info()
        self.db.ensure_model(info)
        fp = info["fingerprint"]
        unit = [1.0 / (8 ** 0.5)] * 8
        self.db.upsert_batch([("a", 1, "h1", fp, unit, 10)])
        stats = self.db.stats(fp)
        self.assertAlmostEqual(stats["min_norm"], 1.0, places=6)
        self.assertEqual(stats["n_wrong_dim"], 0)

    def test_advisory_lock_is_exclusive(self):
        from docpulse.embed.db import PgEmbeddingDb

        other = PgEmbeddingDb(DSN, table=self.table)
        other.connect()
        try:
            with self.db.advisory_lock() as got:
                self.assertTrue(got)
                with other.advisory_lock() as second:
                    self.assertFalse(second, "a second run must not get the lock")
            with other.advisory_lock() as after:
                self.assertTrue(after, "lock must be released")
        finally:
            other.close()

    def test_ledger_round_trip(self):
        info = self.info()
        self.db.ensure_model(info)
        self.db.start_run("run-1", info["fingerprint"], 5, False)
        self.db.finish_run("run-1", "success", {"n_embedded": 5, "n_skipped": 0, "n_failed": 0, "chars_total": 50, "seconds": 1.5})
        runs = self.db.recent_runs(5)
        self.assertEqual(runs[0]["run_id"], "run-1")
        self.assertEqual(runs[0]["status"], "success")
        self.assertEqual(runs[0]["n_embedded"], 5)


if __name__ == "__main__":
    unittest.main()
