"""Postgres + pgvector access.

psycopg is imported lazily so this module can be imported (and the plan/verify
unit tests can run) on a machine without a database driver.

Schema
------
embedding_models  one row per vector space (model id, revision, dim, template)
paper_embeddings  one row per paper, keyed by paper_id, holding the latest vector
embedding_runs    a cost ledger: what each run planned, embedded, skipped, failed

No vector index is created on purpose: at a few thousand rows an exact scan is
faster than HNSW and has no build cost. Add one when you pass ~100k rows.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

SCHEMA_VERSION = 1


def _vector_literal(vec: list[float]) -> str:
    """pgvector accepts '[1,2,3]' text; casting in SQL avoids needing a driver adapter."""
    return "[" + ",".join(f"{v:.7g}" for v in vec) + "]"


class PgEmbeddingDb:
    def __init__(self, dsn: str, table: str = "paper_embeddings", lock_key: int = 811_2026) -> None:
        self.dsn = dsn
        self.table = table
        self.lock_key = lock_key
        self._conn = None
        self._lock_held = False

    # -- connection ------------------------------------------------------
    def connect(self):
        if self._conn is None:
            import psycopg  # lazy

            self._conn = psycopg.connect(self.dsn, autocommit=False)
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None
                self._lock_held = False

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    # -- schema ----------------------------------------------------------
    def ensure_schema(self, dim: int) -> dict:
        """Idempotent. Creates the extension, tables and the model table."""
        conn = self.connect()
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS embedding_models (
                    fingerprint  text PRIMARY KEY,
                    model_id     text NOT NULL,
                    revision     text NOT NULL,
                    dim          integer NOT NULL,
                    normalize    boolean NOT NULL,
                    template     text NOT NULL,
                    kind         text,
                    first_seen   timestamptz NOT NULL DEFAULT now()
                )
                """
            )
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.table} (
                    paper_id      text PRIMARY KEY,
                    version       integer NOT NULL,
                    text_hash     text NOT NULL,
                    model_fp      text NOT NULL REFERENCES embedding_models(fingerprint),
                    embedding     vector({int(dim)}) NOT NULL,
                    chars         integer NOT NULL,
                    embedded_at   timestamptz NOT NULL DEFAULT now()
                )
                """
            )
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS {self.table}_model_fp_idx ON {self.table} (model_fp)"
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS embedding_runs (
                    run_id       text PRIMARY KEY,
                    started_at   timestamptz NOT NULL,
                    finished_at  timestamptz,
                    model_fp     text NOT NULL,
                    dry_run      boolean NOT NULL DEFAULT false,
                    status       text NOT NULL,
                    n_planned    integer NOT NULL DEFAULT 0,
                    n_embedded   integer NOT NULL DEFAULT 0,
                    n_skipped    integer NOT NULL DEFAULT 0,
                    n_failed     integer NOT NULL DEFAULT 0,
                    chars_total  bigint NOT NULL DEFAULT 0,
                    seconds      double precision,
                    notes        jsonb
                )
                """
            )
            # existing table with a different dim => refuse loudly, don't corrupt
            cur.execute(
                """
                SELECT atttypmod FROM pg_attribute
                WHERE attrelid = %s::regclass AND attname = 'embedding'
                """,
                (self.table,),
            )
            row = cur.fetchone()
        conn.commit()
        return {"table": self.table, "dim": dim, "existing_typmod": row[0] if row else None}

    # -- lock ------------------------------------------------------------
    @contextmanager
    def advisory_lock(self) -> Iterator[bool]:
        """Try-lock, not wait: two concurrent runs must not interleave batches."""
        conn = self.connect()
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (self.lock_key,))
            got = bool(cur.fetchone()[0])
        conn.commit()
        self._lock_held = got
        try:
            yield got
        finally:
            if got:
                with conn.cursor() as cur:
                    cur.execute("SELECT pg_advisory_unlock(%s)", (self.lock_key,))
                conn.commit()
                self._lock_held = False

    # -- models ----------------------------------------------------------
    def ensure_model(self, info: dict) -> None:
        conn = self.connect()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO embedding_models (fingerprint, model_id, revision, dim, normalize, template, kind)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (fingerprint) DO NOTHING
                """,
                (
                    info["fingerprint"],
                    info["model_id"],
                    info["model_revision"],
                    int(info["dim"]),
                    bool(info["normalize"]),
                    info["template"],
                    info.get("kind"),
                ),
            )
        conn.commit()

    def model_fingerprints(self) -> dict[str, dict]:
        conn = self.connect()
        with conn.cursor() as cur:
            cur.execute("SELECT fingerprint, model_id, revision, dim, template FROM embedding_models")
            return {
                r[0]: {"model_id": r[1], "revision": r[2], "dim": r[3], "template": r[4]}
                for r in cur.fetchall()
            }

    # -- state -----------------------------------------------------------
    def fetch_state(self) -> dict[str, tuple[str, str]]:
        conn = self.connect()
        with conn.cursor() as cur:
            cur.execute(f"SELECT paper_id, text_hash, model_fp FROM {self.table}")
            return {r[0]: (r[1], r[2]) for r in cur.fetchall()}

    def ids_for_model(self, model_fp: str) -> set[str]:
        conn = self.connect()
        with conn.cursor() as cur:
            cur.execute(f"SELECT paper_id FROM {self.table} WHERE model_fp = %s", (model_fp,))
            return {r[0] for r in cur.fetchall()}

    def text_hashes(self) -> dict[str, str]:
        conn = self.connect()
        with conn.cursor() as cur:
            cur.execute(f"SELECT paper_id, text_hash FROM {self.table}")
            return {r[0]: r[1] for r in cur.fetchall()}

    # -- writes ----------------------------------------------------------
    def upsert_batch(self, rows: list[tuple[str, int, str, str, list[float], int]]) -> int:
        """One transaction per batch: this is the crash-safety boundary."""
        if not rows:
            return 0
        conn = self.connect()
        params = [
            (pid, ver, th, fp, _vector_literal(vec), chars) for (pid, ver, th, fp, vec, chars) in rows
        ]
        with conn.cursor() as cur:
            cur.executemany(
                f"""
                INSERT INTO {self.table} (paper_id, version, text_hash, model_fp, embedding, chars)
                VALUES (%s, %s, %s, %s, %s::vector, %s)
                ON CONFLICT (paper_id) DO UPDATE
                   SET version = EXCLUDED.version,
                       text_hash = EXCLUDED.text_hash,
                       model_fp = EXCLUDED.model_fp,
                       embedding = EXCLUDED.embedding,
                       chars = EXCLUDED.chars,
                       embedded_at = now()
                """,
                params,
            )
        conn.commit()
        return len(params)

    def delete_model_rows(self, model_fp: str) -> int:
        conn = self.connect()
        with conn.cursor() as cur:
            cur.execute(f"DELETE FROM {self.table} WHERE model_fp = %s", (model_fp,))
            n = cur.rowcount
        conn.commit()
        return n

    def delete_ids(self, ids: list[str]) -> int:
        if not ids:
            return 0
        conn = self.connect()
        with conn.cursor() as cur:
            cur.execute(f"DELETE FROM {self.table} WHERE paper_id = ANY(%s)", (ids,))
            n = cur.rowcount
        conn.commit()
        return n

    # -- ledger ----------------------------------------------------------
    def start_run(self, run_id: str, model_fp: str, n_planned: int, dry_run: bool) -> None:
        conn = self.connect()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO embedding_runs (run_id, started_at, model_fp, dry_run, status, n_planned)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (run_id, datetime.now(timezone.utc), model_fp, dry_run, "running", n_planned),
            )
        conn.commit()

    def finish_run(self, run_id: str, status: str, counters: dict, notes: dict | None = None) -> None:
        conn = self.connect()
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE embedding_runs
                   SET finished_at = %s, status = %s, n_embedded = %s, n_skipped = %s,
                       n_failed = %s, chars_total = %s, seconds = %s, notes = %s
                 WHERE run_id = %s
                """,
                (
                    datetime.now(timezone.utc),
                    status,
                    int(counters.get("n_embedded", 0)),
                    int(counters.get("n_skipped", 0)),
                    int(counters.get("n_failed", 0)),
                    int(counters.get("chars_total", 0)),
                    counters.get("seconds"),
                    json.dumps(notes or {}, default=str),
                    run_id,
                ),
            )
        conn.commit()

    def recent_runs(self, n: int = 10) -> list[dict]:
        conn = self.connect()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT run_id, started_at, finished_at, status, n_planned, n_embedded,
                       n_skipped, n_failed, chars_total, seconds, dry_run
                  FROM embedding_runs ORDER BY started_at DESC LIMIT %s
                """,
                (n,),
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    # -- stats -----------------------------------------------------------
    def stats(self, model_fp: str) -> dict:
        conn = self.connect()
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT count(*)                                        AS n_rows,
                       count(*) FILTER (WHERE embedding IS NULL)        AS n_null,
                       min(vector_norm(embedding))                      AS min_norm,
                       max(vector_norm(embedding))                      AS max_norm,
                       count(DISTINCT text_hash)                        AS n_distinct_hashes,
                       count(DISTINCT model_fp)                         AS n_model_fps,
                       coalesce(sum(chars), 0)                          AS chars_total
                  FROM {self.table}
                """
            )
            r = cur.fetchone()
            cols = [d[0] for d in cur.description]
            out = dict(zip(cols, r))
            cur.execute(f"SELECT DISTINCT model_fp FROM {self.table}")
            out["model_fps"] = [x[0] for x in cur.fetchall()]
            cur.execute(
                f"""
                SELECT count(*) FROM {self.table}
                 WHERE embedding IS NOT NULL
                   AND (vector_dims(embedding) <> (SELECT dim FROM embedding_models WHERE fingerprint = %s)
                        OR vector_dims(embedding) IS NULL)
                """,
                (model_fp,),
            )
            out["n_wrong_dim"] = cur.fetchone()[0]
        conn.commit()
        return out


@contextmanager
def open_db(settings) -> Iterator[PgEmbeddingDb]:
    db = PgEmbeddingDb(settings.dsn, table=settings.table, lock_key=settings.lock_key)
    try:
        db.connect()
        yield db
    finally:
        db.close()
