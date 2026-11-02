#!/usr/bin/env python3
"""Day 5 step 3a: the stored weighted tsvector, its GIN index, and an updated index.

Why this and nothing else yet. The probe measured the keyword arm at 1,192-2,832 ms
per query against 4,271 rows, because every query re-parsed every title and abstract
with to_tsvector(). No API can afford that, and no amount of ranking cleverness
changes it. This script makes the tsvector a stored generated column so Postgres
parses each document once, at write time, and the GIN index makes the match a bitmap
lookup instead of a sequential scan.

The weights are the ones the probe validated: title in class A, abstract in class B,
so a query term in the title outranks the same term buried in an abstract.

Idempotent. Every statement is IF NOT EXISTS, so running it twice is a no-op and the
verification block prints the same thing. Nothing is dropped and no row is rewritten
beyond the one-time backfill of the new column.

    python scripts/day5_add_fts.py --dry-run     # print the DDL, touch nothing
    make -f day5.mk fts-migrate

Rollback, if the loader turns out to disagree with an extra column:

    ALTER TABLE paper_meta DROP COLUMN fts;      -- drops the GIN index with it
    DROP INDEX IF EXISTS paper_meta_updated_idx;
"""

from __future__ import annotations

import argparse
import sys
import time

TABLE = "paper_meta"

# Explicit ::regconfig matters: to_tsvector(text, text) is only STABLE, and a
# generated column rejects any expression that is not IMMUTABLE. The two-arg
# to_tsvector(regconfig, text) is immutable; the cast makes the call resolve to it.
FTS_EXPR = (
    "setweight(to_tsvector('english'::regconfig, coalesce(title, '')), 'A') || "
    "setweight(to_tsvector('english'::regconfig, coalesce(abstract, '')), 'B')"
)

DDL: tuple[tuple[str, str], ...] = (
    (
        "add the generated column (one-time backfill of 4,271 rows)",
        f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS fts tsvector "
        f"GENERATED ALWAYS AS ({FTS_EXPR}) STORED",
    ),
    (
        "GIN index on fts: match becomes a bitmap lookup, not a scan",
        f"CREATE INDEX IF NOT EXISTS {TABLE}_fts_idx ON {TABLE} USING gin (fts)",
    ),
    (
        "sort=updated needs an index; published has one, updated does not",
        f"CREATE INDEX IF NOT EXISTS {TABLE}_updated_idx ON {TABLE} (updated DESC NULLS LAST)",
    ),
    (
        "refresh planner statistics for the new column",
        f"ANALYZE {TABLE}",
    ),
)

COLUMN_CHECK = f"""
SELECT a.attname, format_type(a.atttypid, a.atttypmod) AS type, a.attgenerated
  FROM pg_attribute a
 WHERE a.attrelid = '{TABLE}'::regclass AND a.attname = 'fts' AND a.attnum > 0
"""

INDEX_CHECK = f"""
SELECT indexname FROM pg_indexes WHERE schemaname = 'public' AND tablename = '{TABLE}'
 ORDER BY indexname
"""

NULL_CHECK = f"SELECT count(*) FROM {TABLE} WHERE fts IS NULL"

# The generated expression must agree with what the probes computed inline, or the
# new column silently returns different rows than the measurement that justified it.
AGREEMENT_SQL = f"""
SELECT
  (SELECT count(*) FROM {TABLE} WHERE fts @@ to_tsquery('english'::regconfig, %(tsq)s)) AS stored,
  (SELECT count(*) FROM {TABLE}
    WHERE to_tsvector('english'::regconfig,
          coalesce(title, '') || ' ' || coalesce(abstract, ''))
          @@ to_tsquery('english'::regconfig, %(tsq)s)) AS inline
"""

STORED_SQL = f"""
SELECT {TABLE}.arxiv_id, ts_rank_cd('{{0.05,0.2,0.3,1.0}}'::float4[], fts, q) AS score
  FROM {TABLE}, to_tsquery('english'::regconfig, %(tsq)s) AS q
 WHERE fts @@ q
 ORDER BY score DESC
 LIMIT 20
"""

INLINE_SQL = f"""
SELECT {TABLE}.arxiv_id,
       ts_rank_cd('{{0.05,0.2,0.3,1.0}}'::float4[],
                  setweight(to_tsvector('english'::regconfig, coalesce(title, '')), 'A') ||
                  setweight(to_tsvector('english'::regconfig, coalesce(abstract, '')), 'B'),
                  q) AS score
  FROM {TABLE}, to_tsquery('english'::regconfig, %(tsq)s) AS q
 WHERE setweight(to_tsvector('english'::regconfig, coalesce(title, '')), 'A') ||
       setweight(to_tsvector('english'::regconfig, coalesce(abstract, '')), 'B') @@ q
 ORDER BY score DESC
 LIMIT 20
"""

EXPLAIN_SQL = "EXPLAIN (ANALYZE, BUFFERS) " + STORED_SQL

BENCH_TSQ = "sparse | attention | documents"


def print_dry_run() -> None:
    print(f"-- {len(DDL)} statements, all idempotent --")
    for note, sql in DDL:
        print(f"\n-- {note}")
        print(sql + ";")
    print("\n-- verification (after the DDL) --")
    for note, sql in (
        ("column present and generated?", COLUMN_CHECK),
        ("indexes on paper_meta", INDEX_CHECK),
        ("rows with an empty tsvector", NULL_CHECK),
        ("generated column vs inline expression, row counts must match", AGREEMENT_SQL),
    ):
        print(f"\n-- {note}")
        print(sql.strip() + ";")
    print("\n-- timings --")
    print(STORED_SQL.strip() + ";")
    print(INLINE_SQL.strip() + ";")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Add the stored weighted tsvector (idempotent)")
    parser.add_argument("--dry-run", action="store_true", help="print the DDL and exit")
    args = parser.parse_args(argv)

    if args.dry_run:
        print_dry_run()
        return 0

    import psycopg

    from docpulse.embed.config import EmbeddingSettings

    settings = EmbeddingSettings.from_env()

    with psycopg.connect(settings.dsn, connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT version()")
            print(f"postgres   {cur.fetchone()[0].split(',')[0]}")
            cur.execute(f"SELECT count(*) FROM {TABLE}")
            print(f"table      {TABLE} ({int(cur.fetchone()[0])} rows)")
            print("")

            print("-- ddl --")
            for note, sql in DDL:
                started = time.perf_counter()
                cur.execute(sql)
                ms = (time.perf_counter() - started) * 1000
                print(f"   {ms:8.1f} ms  {note}")
            conn.commit()
            print("")

            print("-- verification --")
            cur.execute(COLUMN_CHECK)
            row = cur.fetchone()
            if not row:
                print("   FAIL  column fts does not exist")
                return 1
            name, dtype, generated = row
            print(f"   column     {name} {dtype} "
                  f"({'GENERATED ALWAYS ... STORED' if generated else 'NOT GENERATED'})")
            if not generated:
                return 1

            cur.execute(INDEX_CHECK)
            indexes = [r[0] for r in cur.fetchall()]
            print(f"   indexes    {', '.join(indexes)}")
            for required in (f"{TABLE}_fts_idx", f"{TABLE}_updated_idx"):
                print(f"   {'ok  ' if required in indexes else 'FAIL'}       {required}")

            cur.execute(NULL_CHECK)
            empty = int(cur.fetchone()[0])
            print(f"   empty      {empty} rows with a null tsvector "
                  f"({'ok' if empty == 0 else 'unexpected'})")

            cur.execute(AGREEMENT_SQL, {"tsq": BENCH_TSQ})
            stored, inline = (int(v) for v in cur.fetchone())
            verdict = "match" if stored == inline else "MISMATCH - do not ship"
            print(f"   agreement  stored {stored} / inline {inline}  ({verdict})")
            if stored != inline:
                return 1
            print("")

            print("-- timings (same query, same tsquery) --")
            for label, sql in (("inline", INLINE_SQL), ("stored", STORED_SQL)):
                best = None
                for _ in range(3):
                    started = time.perf_counter()
                    cur.execute(sql, {"tsq": BENCH_TSQ})
                    cur.fetchall()
                    ms = (time.perf_counter() - started) * 1000
                    best = ms if best is None else min(best, ms)
                print(f"   {label:<7} {best:7.1f} ms   (best of 3)")

            print("")
            print("-- plan for the stored query --")
            cur.execute(EXPLAIN_SQL, {"tsq": BENCH_TSQ})
            for (line,) in cur.fetchall():
                print("   " + line)

    print("")
    print("Next:  make -f day4.mk search-meta   (expect 4271/4271 - proves the loader")
    print("       still agrees with paper_meta now that it has one more column)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
