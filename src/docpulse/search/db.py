"""Day 4: Postgres/pgvector queries for the search API.

Two tables are involved and they answer different questions:

  paper_embeddings  the vectors, written by Day 3 (never written here).
                    Its key is ``paper_id``, not ``arxiv_id``; the SQL aliases it
                    so the JSON can stay domain-shaped.
  paper_meta        title/abstract/categories/dates, loaded from silver

Keeping metadata in Postgres rather than re-reading parquet per request is what
makes a query a single round trip. ``scripts/day4_load_meta.py`` fills it.

Scoring is cosine similarity on unit-normalised vectors: ``1 - (a <=> b)``.
Sequential scan on purpose at this corpus size; ``make -f day4.mk search-index``
creates the HNSW index when the table grows past the point where it pays.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Iterable, Sequence

SEARCH_SQL = """
SELECT e.{idcol} AS arxiv_id,
       1 - (e.embedding <=> %(vec)s::vector) AS score,
       e.version,
       m.title,
       m.abstract,
       m.categories,
       m.published,
       m.updated
  FROM {table} e
  LEFT JOIN {meta} m ON m.arxiv_id = e.{idcol}
 WHERE e.embedding IS NOT NULL
   AND e.model_fp = %(fp)s
   AND (%(category)s::text IS NULL
        OR %(category)s::text = ANY(string_to_array(coalesce(m.categories, ''), ' ')))
   AND (%(since)s::timestamptz IS NULL OR m.published >= %(since)s::timestamptz)
   AND (%(until)s::timestamptz IS NULL OR m.published <  %(until)s::timestamptz)
 ORDER BY e.embedding <=> %(vec)s::vector
 LIMIT %(limit)s
"""

PAPER_SQL = """
SELECT m.arxiv_id, m.title, m.abstract, m.categories, m.published, m.updated,
       e.version, e.model_fp, (e.embedding IS NOT NULL) AS has_vector
  FROM {meta} m
  LEFT JOIN {table} e ON e.{idcol} = m.arxiv_id
 WHERE m.arxiv_id = %(id)s
"""

COUNT_SQL = "SELECT count(*) FROM {meta}"


def vector_literal(values: Sequence[float]) -> str:
    """pgvector accepts a string like '[0.1,0.2,...]' cast to ::vector.

    Six decimals is well inside float32 precision and keeps the literal small
    (384 dims -> ~3.5 kB instead of ~8 kB).
    """
    return "[" + ",".join(f"{float(v):.6f}" for v in values) + "]"


class PgSearchDb:
    """One connection, guarded by a lock.

    A pool would be the right answer for a multi-worker deployment; for a
    single-process service this is simpler and has no extra dependency. The lock
    matters because FastAPI runs sync endpoints in a threadpool.
    """

    def __init__(
        self,
        dsn: str,
        *,
        table: str = "paper_embeddings",
        meta_table: str = "paper_meta",
        id_column: str = "paper_id",  # Day 3's primary key; aliased to arxiv_id in JSON
    ):
        self.dsn = dsn
        self.table = table
        self.meta_table = meta_table
        self.id_column = id_column
        self._lock = threading.Lock()
        self._conn = None

    # -- lifecycle -------------------------------------------------------
    def connect(self):
        if self._conn is None:
            import psycopg

            self._conn = psycopg.connect(self.dsn, connect_timeout=10, autocommit=True)
        return self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                finally:
                    self._conn = None

    def _cursor(self):
        return self.connect().cursor()

    # -- queries ---------------------------------------------------------
    def search(
        self,
        *,
        vector: Sequence[float],
        model_fp: str,
        limit: int,
        category: str | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = SEARCH_SQL.format(table=self.table, meta=self.meta_table, idcol=self.id_column)
        params = {
            "vec": vector_literal(vector),
            "fp": model_fp,
            "limit": int(limit),
            # empty string from a query parameter must mean "no filter"
            "category": (category or None) or None,
            "since": (since or None) or None,
            "until": (until or None) or None,
        }
        with self._lock, self._cursor() as cur:
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
        for row in rows:
            row["score"] = float(row["score"])
        return rows

    def paper(self, arxiv_id: str) -> dict[str, Any] | None:
        sql = PAPER_SQL.format(table=self.table, meta=self.meta_table, idcol=self.id_column)
        with self._lock, self._cursor() as cur:
            cur.execute(sql, {"id": arxiv_id})
            row = cur.fetchone()
            if row is None:
                return None
            cols = [d[0] for d in cur.description]
        return dict(zip(cols, row, strict=True))

    def health(self, model_fp: str) -> dict[str, Any]:
        """Never raises: /health must answer even when the database is gone."""
        started = time.time()
        out: dict[str, Any] = {
            "db": "down",
            "n_vectors": 0,
            "n_meta": 0,
            "metadata_loaded": False,
            "model_matches": False,
            "took_ms": 0,
        }
        try:
            with self._lock, self._cursor() as cur:
                cur.execute(f"SELECT count(*) FROM {self.table}")
                out["n_vectors"] = int(cur.fetchone()[0])
                cur.execute("SELECT to_regclass(%s) IS NOT NULL", (f"public.{self.meta_table}",))
                meta_exists = bool(cur.fetchone()[0])
                out["metadata_loaded"] = meta_exists
                if meta_exists:
                    cur.execute(COUNT_SQL.format(meta=self.meta_table))
                    out["n_meta"] = int(cur.fetchone()[0])
                cur.execute(f"SELECT DISTINCT model_fp FROM {self.table} LIMIT 2")
                fps = [r[0] for r in cur.fetchall()]
                out["model_matches"] = fps == [model_fp]
                out["model_fps"] = fps
            out["db"] = "up"
        except Exception as exc:  # noqa: BLE001 - health reports, it does not raise
            out["error"] = f"{type(exc).__name__}: {exc}"
        out["took_ms"] = round((time.time() - started) * 1000, 1)
        return out

    def stats(self, model_fp: str) -> dict[str, Any]:
        with self._lock, self._cursor() as cur:
            cur.execute(
                f"""
                SELECT count(*) AS n_rows,
                       count(DISTINCT e.{self.id_column}) AS n_papers,
                       avg(vector_dims(e.embedding)) AS avg_dims
                  FROM {self.table} e
                 WHERE e.embedding IS NOT NULL
                """
            )
            cols = [d[0] for d in cur.description]
            row = dict(zip(cols, cur.fetchone(), strict=True))
            cur.execute("SELECT to_regclass(%s) IS NOT NULL", (f"public.{self.meta_table}",))
            if cur.fetchone()[0]:
                cur.execute(COUNT_SQL.format(meta=self.meta_table))
                row["n_meta"] = int(cur.fetchone()[0])
        row["model_fp"] = model_fp
        row["avg_dims"] = float(row["avg_dims"]) if row.get("avg_dims") is not None else None
        return row


def rows_to_results(rows: Iterable[dict[str, Any]], *, include_abstract: bool = False) -> list[dict]:
    """Shape DB rows into the JSON the API promises (and keep it small)."""
    out = []
    for row in rows:
        item = {
            "arxiv_id": row["arxiv_id"],
            "title": row.get("title"),
            "categories": row.get("categories"),
            "published": _iso(row.get("published")),
            "updated": _iso(row.get("updated")),
            "version": row.get("version"),
            "score": round(float(row["score"]), 6),
        }
        if include_abstract:
            item["abstract"] = row.get("abstract")
        out.append(item)
    return out


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    iso = getattr(value, "isoformat", None)
    return iso() if callable(iso) else str(value)
