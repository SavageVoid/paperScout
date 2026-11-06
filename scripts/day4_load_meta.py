#!/usr/bin/env python3
"""Day 4/7: load paper metadata from silver into Postgres — a thin CLI.

The work lives in :mod:`docpulse.silver.load`, which the DAG calls too. This file
kept its own copy of the mapping, the SQL and the author handling until Day 7, and
the copies drifted: the script folded arXiv's markup out of author names, the
promoted path did not, and a reload silently moved the authors fingerprint while
every check stayed green. One implementation, two callers.

Kept here, deliberately:

    --dry-run   report what would be written, write nothing
    --limit N   stop after N rows (sampling)
    --batch N   rows per executemany
    --prune     DELETE rows absent from silver -- never in the DAG: a scheduled
                delete keyed on "whatever is in silver right now" will one day run
                against a half-built silver, and paper_embeddings has no foreign key
                back to paper_meta to stop it.

    make -f day4.mk search-meta
    python scripts/day4_load_meta.py --dry-run
"""

from __future__ import annotations

import argparse
import sys

from docpulse.silver.load import (
    DEFAULT_BATCH,
    META_TABLE,
    build_rows_from_silver,
    load_meta,
    read_silver_rows,
)


def _prune(arxiv_ids: list[str], dsn: str, meta_table: str) -> int:
    import psycopg

    with psycopg.connect(dsn, connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"DELETE FROM {meta_table} WHERE arxiv_id NOT IN "
                f"(SELECT unnest(%s::text[]))",
                (arxiv_ids,),
            )
            pruned = cur.rowcount
        conn.commit()
    return pruned


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Load silver metadata into postgres (Day 4/7 thin CLI)")
    parser.add_argument("--dry-run", action="store_true", help="read and report, write nothing")
    parser.add_argument("--prune", action="store_true", help="delete rows absent from silver")
    parser.add_argument("--limit", type=int, default=None, help="stop after N rows (testing)")
    parser.add_argument("--batch", type=int, default=DEFAULT_BATCH)
    args = parser.parse_args(argv)

    try:
        raw_rows, facts = read_silver_rows()
    except Exception as exc:  # noqa: BLE001 - report, do not traceback a credentials miss
        print(f"cannot read silver: {type(exc).__name__}: {exc}")
        print("If this says InvalidAccessKeyId, the container is missing the S3 credentials")
        print("(AWS_ACCESS_KEY_ID / S3_ENDPOINT_URL) — see docker-compose.day4.yml.")
        return 2

    rows = build_rows_from_silver(raw_rows)
    if args.limit:
        rows = rows[:args.limit]

    print(f"silver        {facts['manifest_key']}")
    print(f"rows read     {facts['n_rows_read']} from {facts['n_months']} month file(s)")
    print(f"unique papers {len(rows)}")
    without_authors = sum(1 for r in rows if not r["authors"])
    print(f"without title {sum(1 for r in rows if not r['title'])}")
    print(f"without authors {without_authors}")

    if args.dry_run:
        print("--dry-run: nothing written")
        return 0

    report = load_meta(rows, batch=args.batch, meta_table=META_TABLE)
    print(f"wrote         {report['n_written']} rows into {report['meta_table']}")

    if args.prune:
        from docpulse.embed.config import EmbeddingSettings

        pruned = _prune([r["arxiv_id"] for r in rows], EmbeddingSettings.from_env().dsn,
                        META_TABLE)
        print(f"pruned        {pruned} rows absent from silver")
    return 0


if __name__ == "__main__":
    sys.exit(main())
