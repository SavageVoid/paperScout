#!/usr/bin/env python3
"""Day 6 stage 2: put author names on paper_meta, version-aware, idempotent.

Reads bronze from the object store, keeps the newest version of each paper, folds
the LaTeX artefacts out of names, and writes two columns:

    authors      text[]    display form, original order, exactly as arXiv gave it
    authors_tsv  tsvector  generated with the 'simple' config (never 'english')

Names are NOT folded into the existing weighted `fts` column. That column is what
the Day-5 keyword probe measured, and changing it would invalidate those numbers.

Dry run first -- it touches no database and prints the measurements:

    docker compose ... exec searchapi python /tmp/day6_add_authors.py --s3 --dry-run
    docker compose ... exec searchapi python /tmp/day6_add_authors.py --s3 --apply

Why the newest version wins: the bronze harvest returned 13,012 records for 4,271
papers. Keeping whichever record was read first would preserve stale author lists
whenever a revision added or removed someone. The dry run reports how many papers
that decision actually changes, so the rule is measured, not assumed.
"""

from __future__ import annotations

import argparse
import collections
import re
import sys
from xml.sax.saxutils import unescape

ARXIV_ID_RE = re.compile(
    r"(?:abs/)?([0-9]{4}\.[0-9]{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/[0-9]{7})(?:v([0-9]+))?"
)
ENTRY_SPLIT_RE = re.compile(r"<entry\b.*?</entry>", re.S)
AUTHOR_RE = re.compile(r"<author\b.*?<name>\s*(.*?)\s*</name>.*?</author>", re.S)
ID_RE = re.compile(r"<id>\s*(.*?)\s*</id>", re.S)

# arXiv renders some accents as a brace-wrapped character: "Herv{é} Panetto",
# "Łukasz Smoli{ń}ski". Left alone, those names can never match a typed "Hervé".
SINGLE_CHAR_BRACES_RE = re.compile(r"\{([^{}\\\\])\}")
# The older LaTeX spelling: M{\"u}ller, Dvo{\v r}{\'a}k.
LATEX_ACCENT_RE = re.compile(r"\{\\([`'^\"~=.])\s*\{?([A-Za-z])\}?\}")
ACCENT_MAP = {"'": "\u0301", "`": "\u0300", "^": "\u0302", '"': "\u0308",
              "~": "\u0303", "=": "\u0304", ".": "\u0307"}


def clean_name(name: str) -> str:
    name = unescape(name)
    name = LATEX_ACCENT_RE.sub(
        lambda m: _compose(m.group(2), ACCENT_MAP.get(m.group(1), "")), name)
    name = SINGLE_CHAR_BRACES_RE.sub(r"\1", name)
    name = name.replace("{", "").replace("}", "").replace("\\", "")
    return " ".join(name.split())


def _compose(letter: str, combining: str) -> str:
    import unicodedata
    return unicodedata.normalize("NFC", letter + combining) if combining else letter


def parse_blob(blob: bytes):
    """Yield (paper_id, version, [names]) for every entry in a bronze object."""
    import gzip

    if blob[:2] == b"\x1f\x8b":
        try:
            blob = gzip.decompress(blob)
        except OSError:
            return
    text = blob.decode("utf-8", errors="replace")
    if not ("<entry" in text[:4096] or "<OAI-PMH" in text[:4096] or "<feed" in text[:4096]):
        return
    for fragment in ENTRY_SPLIT_RE.findall(text):
        found = ID_RE.search(fragment)
        if not found:
            continue
        match = ARXIV_ID_RE.search(unescape(found.group(1)))
        if not match:
            continue
        paper_id = match.group(1)
        version = int(match.group(2) or 0)
        names = [clean_name(raw) for raw in AUTHOR_RE.findall(fragment)]
        yield paper_id, version, [n for n in names if n]


def collect(store, prefix: str, limit: int) -> tuple[dict, dict]:
    """Newest version per paper, and a tally of what that decision changed."""
    newest: dict[str, tuple[int, list[str]]] = {}
    first_seen: dict[str, list[str]] = {}
    records = 0
    entries = 0
    for key in store.list_keys(prefix):
        if key.endswith("/"):
            continue
        if records >= limit:
            break
        records += 1
        try:
            blob = store.get_bytes(key)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! {key}: {exc}")
            continue
        for paper_id, version, names in parse_blob(blob):
            entries += 1
            first_seen.setdefault(paper_id, names)
            held = newest.get(paper_id)
            if held is None or version >= held[0]:
                newest[paper_id] = (version, names)
    stats = {
        "objects": records,
        "entries": entries,
        "first_seen": first_seen,
    }
    return newest, stats


DDL = (
    "ALTER TABLE paper_meta ADD COLUMN IF NOT EXISTS authors text[]",
    "ALTER TABLE paper_meta ADD COLUMN IF NOT EXISTS authors_tsv tsvector "
    "GENERATED ALWAYS AS (to_tsvector('simple', "
    "coalesce(array_to_string(authors, ' '), ''))) STORED",
    "CREATE INDEX IF NOT EXISTS paper_meta_authors_idx "
    "ON paper_meta USING GIN (authors)",
    "CREATE INDEX IF NOT EXISTS paper_meta_authors_tsv_idx "
    "ON paper_meta USING GIN (authors_tsv)",
)

CHECKS = (
    ("papers with a NULL authors column", "SELECT count(*) FROM paper_meta WHERE authors IS NULL"),
    ("papers with an empty authors array", "SELECT count(*) FROM paper_meta WHERE authors = '{}'"),
    ("Yann LeCun", "SELECT count(*) FROM paper_meta WHERE authors @> ARRAY['Yann LeCun']"),
    ("Geoffrey Hinton", "SELECT count(*) FROM paper_meta WHERE authors @> ARRAY['Geoffrey Hinton']"),
    ("partial: 'lecun' via simple tsvector",
     "SELECT count(*) FROM paper_meta WHERE authors_tsv @@ plainto_tsquery('simple', 'lecun')"),
)


def resolve_dsn() -> str:
    import os

    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    parts = {
        "host": os.environ.get("POSTGRES_HOST", "postgres"),
        "dbname": os.environ.get("POSTGRES_DB", "docpulse"),
        "user": os.environ.get("POSTGRES_USER", "docpulse"),
        "password": os.environ.get("POSTGRES_PASSWORD", ""),
    }
    return " ".join(f"{k}={v}" for k, v in parts.items() if v)


def report(newest: dict, stats: dict, check: list[str]) -> None:
    first_seen = stats["first_seen"]
    all_names = [n for _, names in newest.values() for n in names]
    sizes = [len(names) for _, names in newest.values()]
    grew = [pid for pid, (_, names) in newest.items()
            if len(names) > len(first_seen.get(pid, []))]
    shrank = [pid for pid, (_, names) in newest.items()
              if len(names) < len(first_seen.get(pid, []))]
    braced = collections.Counter(n for n in all_names if "{" in n or "\\" in n)

    print("\n== collected ==")
    print(f"objects read            {stats['objects']}")
    print(f"records parsed          {stats['entries']}")
    print(f"papers                  {len(newest)}")
    print(f"duplicate records       {stats['entries'] - len(newest)}  ({stats['entries'] / max(len(newest), 1):.2f}x per paper)")
    print(f"authors per paper       mean {sum(sizes) / len(sizes):.2f}  max {max(sizes)}")
    print(f"name entries            {len(all_names)}   distinct {len(set(all_names))}")

    print("\n== what 'newest version wins' actually changes ==")
    print(f"  gained authors        {len(grew)}  {grew[:6]}")
    print(f"  lost authors          {len(shrank)}  {shrank[:6]}")
    if not grew and not shrank:
        print("  nothing: every version of every paper carries the same author list")

    print("\n== name cleaning ==")
    print(f"  names still braced    {len(braced)}  {list(braced)[:6]}")
    if braced:
        print("  ! these would never match a typed name; extend clean_name() first")

    print("\n== acceptance names ==")
    lowered = collections.Counter(n.lower() for n in all_names)
    for wanted in check:
        key = " ".join(wanted.split()).lower()
        print(f"  {wanted:22s} {lowered.get(key, 0):4d} papers")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Backfill authors onto paper_meta")
    parser.add_argument("--prefix", default="bronze")
    parser.add_argument("--limit", type=int, default=500, help="max objects to read")
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--apply", dest="dry_run", action="store_false")
    parser.add_argument("--check", nargs="*",
                        default=["Yann LeCun", "Geoffrey Hinton"])
    parser.add_argument("--s3", action="store_true", default=True)
    parser.add_argument("--emit", metavar="TSV",
                        help="write arxiv_id/ord/name rows here and exit (no database)")
    args = parser.parse_args(argv)

    from docpulse.config import StorageSettings
    from docpulse.storage import S3Store

    store = S3Store.from_settings(StorageSettings.from_env())
    newest, stats = collect(store, args.prefix, args.limit)
    if not newest:
        print(f"ERROR: no records under {args.prefix!r}")
        return 2
    report(newest, stats, args.check)

    if args.emit:
        from pathlib import Path

        rows = 0
        with Path(args.emit).open("w", encoding="utf-8") as handle:
            for paper_id, (_, names) in newest.items():
                for position, name in enumerate(names):
                    # names are whitespace-collapsed, so no tab or newline can
                    # appear here and the TSV needs no quoting at all
                    handle.write(f"{paper_id}\t{position}\t{name}\n")
                    rows += 1
        print(f"\nwrote {rows} rows for {len(newest)} papers -> {args.emit}")
        print("load it with scripts/day6_add_authors.sql (no DNS, no psycopg)")
        return 0

    if args.dry_run:
        print("\n--dry-run: no database was touched.")
        print("Next: --emit /tmp/authors.tsv, then load it with day6_add_authors.sql,")
        print("or --apply if this container can reach the database.")
        return 0

    import psycopg

    dsn = resolve_dsn()
    print(f"\n== applying to {dsn.split('password')[0].strip()} ==")
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            for statement in DDL:
                cur.execute(statement)
                print(f"  ok  {statement.split('(')[0].strip()[:70]}")
            payload = [(names, pid) for pid, (_, names) in newest.items()]
            cur.executemany(
                "UPDATE paper_meta SET authors = %s WHERE arxiv_id = %s", payload)
            print(f"  sent {len(payload)} rows; the checks below say how many landed")
        conn.commit()
        with conn.cursor() as cur:
            print("\n== checks ==")
            for label, sql in CHECKS:
                cur.execute(sql)
                print(f"  {label:38s} {cur.fetchone()[0]}")

    print("\nNext: run the author probe (stage 6.3) before touching /search.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
