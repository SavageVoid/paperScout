#!/usr/bin/env python3
"""Day 4: load paper metadata from silver into Postgres.

The search API answers in one round trip because titles and abstracts live in
Postgres next to the vectors, not in parquet. This script is the bridge, and it
is idempotent: re-running updates rows in place and (with ``--prune``) removes
papers that no longer exist in silver.

    make -f day4.mk search-meta
    python scripts/day4_load_meta.py --dry-run
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS {meta} (
    arxiv_id         text PRIMARY KEY,
    title            text,
    abstract         text,
    categories       text,
    primary_category text,
    published        timestamptz,
    updated          timestamptz,
    version          integer,
    text_hash        text,
    loaded_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS {meta}_published_idx  ON {meta} (published);
CREATE INDEX IF NOT EXISTS {meta}_categories_idx ON {meta} (categories);
"""

UPSERT = """
INSERT INTO {meta} (arxiv_id, title, abstract, categories, primary_category,
                    published, updated, version, text_hash)
VALUES (%(arxiv_id)s, %(title)s, %(abstract)s, %(categories)s, %(primary_category)s,
        %(published)s::timestamptz, %(updated)s::timestamptz, %(version)s, %(text_hash)s)
ON CONFLICT (arxiv_id) DO UPDATE SET
       title = excluded.title,
       abstract = excluded.abstract,
       categories = excluded.categories,
       primary_category = excluded.primary_category,
       published = excluded.published,
       updated = excluded.updated,
       version = excluded.version,
       text_hash = excluded.text_hash,
       loaded_at = now()
"""


def _pick(row: dict, keys) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def normalise_categories(value: Any) -> str | None:
    """Store categories as a space-separated token list, whatever silver hands us.

    pyarrow gives a Python list, which ``str()`` would turn into the literal text
    "['cs.CL', 'cs.CV']". The search filter splits on spaces, so that text matches
    nothing and ``?category=cs.LG`` quietly returns zero rows. Normalising here
    keeps the query simple and the data honest.
    """
    if value is None:
        return None
    if isinstance(value, (list, tuple, set)):
        items = [str(v) for v in value]
    else:
        text = str(value).strip()
        if not text:
            return None
        if text.startswith("[") and text.endswith("]"):
            try:
                parsed = ast.literal_eval(text)
            except (ValueError, SyntaxError):
                parsed = None
            items = [str(v) for v in parsed] if isinstance(parsed, (list, tuple)) else text.split()
        elif "," in text and " " not in text:
            items = text.split(",")
        else:
            items = text.split()
    tokens: list[str] = []
    for item in items:
        token = item.strip().strip("'\"[](),")
        if token and token not in tokens:
            tokens.append(token)
    return " ".join(tokens) or None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    return value if isinstance(value, str) else str(value)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value or None
    iso = getattr(value, "isoformat", None)
    return iso() if callable(iso) else str(value)


def build_rows(raw_rows: list[dict], *, id_keys, version_keys, hash_keys) -> list[dict]:
    """Map silver's columns onto the ``paper_meta`` shape. Pure: easy to test."""
    out: dict[str, dict] = {}
    for raw in raw_rows:
        paper_id = _pick(raw, id_keys)
        if paper_id is None:
            continue
        try:
            version = int(_pick(raw, version_keys) or 1)
        except (TypeError, ValueError):
            version = 1
        row = {
            "arxiv_id": str(paper_id).strip(),
            "title": _text(raw.get("title")),
            "abstract": _text(raw.get("abstract")),
            "categories": normalise_categories(raw.get("categories")),
            "primary_category": _text(raw.get("primary_category")),
            "published": _iso(raw.get("published")),
            "updated": _iso(raw.get("updated")),
            "version": version,
            "text_hash": _text(_pick(raw, hash_keys)),
        }
        # highest version wins, same rule as the embedding build
        current = out.get(row["arxiv_id"])
        if current is None or row["version"] >= current["version"]:
            out[row["arxiv_id"]] = row
    return sorted(out.values(), key=lambda r: r["arxiv_id"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load silver metadata into postgres (Day 4)")
    parser.add_argument("--dry-run", action="store_true", help="read and report, write nothing")
    parser.add_argument("--prune", action="store_true", help="delete rows absent from silver")
    parser.add_argument("--limit", type=int, default=None, help="stop after N rows (testing)")
    parser.add_argument("--batch", type=int, default=500)
    args = parser.parse_args(argv)

    from docpulse.embed import compat
    from docpulse.embed.config import EmbeddingSettings

    settings = EmbeddingSettings.from_env()
    meta_table = "paper_meta"

    store = compat.get_store()
    try:
        manifest_key = compat.find_silver_manifest_key(store)
        manifest = json.loads(store.get_bytes(manifest_key))
    except Exception as exc:
        print(f"cannot read silver: {type(exc).__name__}: {exc}")
        print("If this says InvalidAccessKeyId, the container is missing the S3 credentials")
        print("(AWS_ACCESS_KEY_ID / S3_ENDPOINT_URL) — see docker-compose.day4.yml.")
        return 2

    months = manifest.get("months") or manifest.get("parts") or manifest.get("files") or []
    raw_rows: list[dict] = []
    for item in months:
        key = item if isinstance(item, str) else (item.get("key") or item.get("path"))
        if not key:
            continue
        decoded, _mode = compat._decode_month(store, key)
        raw_rows.extend(decoded)

    rows = build_rows(
        raw_rows,
        id_keys=compat.ID_KEYS,
        version_keys=compat.VERSION_KEYS,
        hash_keys=compat.HASH_KEYS,
    )
    if args.limit:
        rows = rows[: args.limit]

    print(f"silver        {manifest_key}")
    print(f"rows read     {len(raw_rows)} from {len(months)} month file(s)")
    print(f"unique papers {len(rows)}")
    missing = sum(1 for r in rows if not r["title"])
    print(f"without title {missing}")
    if args.dry_run:
        print("--dry-run: nothing written")
        return 0

    import psycopg

    written = 0
    with psycopg.connect(settings.dsn, connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA.format(meta=meta_table))
            upsert = UPSERT.format(meta=meta_table)
            for start in range(0, len(rows), max(1, args.batch)):
                batch = rows[start : start + max(1, args.batch)]
                cur.executemany(upsert, batch)
                written += len(batch)
                print(f"  wrote {written}/{len(rows)}", end="\r", flush=True)
            pruned = 0
            if args.prune:
                cur.execute(
                    f"DELETE FROM {meta_table} WHERE arxiv_id NOT IN "
                    f"(SELECT unnest(%s::text[]))",
                    ([r["arxiv_id"] for r in rows],),
                )
                pruned = cur.rowcount
        conn.commit()

    print(f"\nwrote         {written} rows into {meta_table}")
    if args.prune:
        print(f"pruned        {pruned} rows no longer in silver")
    return 0


if __name__ == "__main__":
    sys.exit(main())
