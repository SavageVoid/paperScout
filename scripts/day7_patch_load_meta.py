#!/usr/bin/env python3
"""Day 7 stage 2: teach the paper_meta loader to write authors (audit I-01, I-05).

Authors were backfilled once by a script that re-parsed bronze and re-implemented
silver's newest-version rule. That leaves two sources of truth and one manual hop:
the normal load path writes rows with no authors at all, so the next arXiv
announcement silently produces papers that author search cannot find.

This patch carries authors through the existing loader instead:

  * SCHEMA learns the three columns, so a fresh database matches a migrated one;
  * UPSERT writes authors, authors_ci and authors_tsv on insert *and* on conflict,
    so a revision that changes the author list refreshes it;
  * build_rows maps silver's authors list, deriving authors_ci with the same
    normalise() the exact-match query uses -- one definition, not two.

authors_tsv is built in SQL with the very expression day6_add_authors.sql used, so
a row loaded here and a row backfilled there produce identical tsvectors.
Author order and duplicates are preserved exactly as silver has them.

    python3 scripts/day7_patch_load_meta.py --dry-run
    python3 scripts/day7_patch_load_meta.py
"""

from __future__ import annotations

import argparse
import ast
import difflib
import pathlib
import sys

TARGET = pathlib.Path("scripts/day4_load_meta.py")

SCHEMA_OLD = '''    version          integer,
    text_hash        text,
    loaded_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS {meta}_published_idx  ON {meta} (published);
CREATE INDEX IF NOT EXISTS {meta}_categories_idx ON {meta} (categories);
"""'''

SCHEMA_NEW = '''    version          integer,
    text_hash        text,
    authors          text[],
    authors_ci       text[],
    authors_tsv      tsvector,
    loaded_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS {meta}_published_idx  ON {meta} (published);
CREATE INDEX IF NOT EXISTS {meta}_categories_idx ON {meta} (categories);
CREATE INDEX IF NOT EXISTS {meta}_authors_idx     ON {meta} USING GIN (authors);
CREATE INDEX IF NOT EXISTS {meta}_authors_ci_idx  ON {meta} USING GIN (authors_ci);
CREATE INDEX IF NOT EXISTS {meta}_authors_tsv_idx ON {meta} USING GIN (authors_tsv);
"""'''

UPSERT_OLD = '''INSERT INTO {meta} (arxiv_id, title, abstract, categories, primary_category,
                    published, updated, version, text_hash)
VALUES (%(arxiv_id)s, %(title)s, %(abstract)s, %(categories)s, %(primary_category)s,
        %(published)s::timestamptz, %(updated)s::timestamptz, %(version)s, %(text_hash)s)
ON CONFLICT (arxiv_id) DO UPDATE SET'''

UPSERT_NEW = '''INSERT INTO {meta} (arxiv_id, title, abstract, categories, primary_category,
                    published, updated, version, text_hash,
                    authors, authors_ci, authors_tsv)
VALUES (%(arxiv_id)s, %(title)s, %(abstract)s, %(categories)s, %(primary_category)s,
        %(published)s::timestamptz, %(updated)s::timestamptz, %(version)s, %(text_hash)s,
        %(authors)s::text[], %(authors_ci)s::text[],
        to_tsvector('simple',
                    array_to_string(coalesce(%(authors)s::text[], ARRAY[]::text[]), ' ')))
ON CONFLICT (arxiv_id) DO UPDATE SET'''

SET_OLD = '''       text_hash = excluded.text_hash,
       loaded_at = now()'''

SET_NEW = '''       text_hash = excluded.text_hash,
       authors = excluded.authors,
       authors_ci = excluded.authors_ci,
       authors_tsv = excluded.authors_tsv,
       loaded_at = now()'''

HELPER_OLD = '''def build_rows(raw_rows: list[dict], *, id_keys, version_keys, hash_keys) -> list[dict]:'''

HELPER_NEW = '''def _authors(raw: dict) -> list[str]:
    """Silver's author list, whatever pyarrow hands us.

    pyarrow gives a list, but a round trip through parquet or json can hand back
    its repr -- the same trap ``normalise_categories`` describes. Order is
    preserved and duplicates are kept: they exist in the source, and inventing a
    cleaner corpus than arXiv ships would be a quiet lie.

    ``authors_ci`` is derived from these names with ``normalise``, the function the
    exact-match query uses, so a row written here matches a query built there.
    """
    value = raw.get("authors")
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        items = [str(v) for v in value]
    else:
        text = str(value).strip()
        if not text:
            return []
        if text.startswith("[") and text.endswith("]"):
            try:
                parsed = ast.literal_eval(text)
            except (ValueError, SyntaxError):
                parsed = None
            items = ([str(v) for v in parsed]
                     if isinstance(parsed, (list, tuple))
                     else text.split(","))
        elif "," in text:
            items = text.split(",")
        else:
            items = [text]

    names: list[str] = []
    for item in items:
        name = " ".join(item.strip().strip("'\\"").split())
        if name:
            names.append(name)
    return names


def build_rows(raw_rows: list[dict], *, id_keys, version_keys, hash_keys) -> list[dict]:'''

ROW_OLD = '''        row = {
            "arxiv_id": str(paper_id).strip(),
            "title": _text(raw.get("title")),
            "abstract": _text(raw.get("abstract")),
            "categories": normalise_categories(raw.get("categories")),
            "primary_category": _text(raw.get("primary_category")),
            "published": _iso(raw.get("published")),
            "updated": _iso(raw.get("updated")),
            "version": version,
            "text_hash": _text(_pick(raw, hash_keys)),
        }'''

ROW_NEW = '''        authors = _authors(raw)
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
            "authors": authors,
            "authors_ci": [normalise(a) for a in authors],
        }'''

IMPORT_OLD = '''    """Map silver's columns onto the ``paper_meta`` shape. Pure: easy to test."""
    out: dict[str, dict] = {}'''

IMPORT_NEW = '''    """Map silver's columns onto the ``paper_meta`` shape. Pure: easy to test."""
    from docpulse.search.authors import normalise

    out: dict[str, dict] = {}'''

EDITS = (
    ("SCHEMA gains the three author columns and their GIN indexes", SCHEMA_OLD, SCHEMA_NEW),
    ("UPSERT writes the three columns", UPSERT_OLD, UPSERT_NEW),
    ("ON CONFLICT refreshes them on a revision", SET_OLD, SET_NEW),
    ("_authors() helper added", HELPER_OLD, HELPER_NEW),
    ("build_rows maps authors and authors_ci", ROW_OLD, ROW_NEW),
    ("build_rows imports the shared normalise", IMPORT_OLD, IMPORT_NEW),
)


def patch(text: str) -> tuple[str, list[str], list[str]]:
    applied, skipped = [], []
    for name, old, new in EDITS:
        if new in text:
            skipped.append(name)
            continue
        hits = text.count(old)
        if hits != 1:
            raise SystemExit(
                f"ERROR: anchor for {name!r} appears {hits} times, expected exactly 1.\n"
                "Refusing to guess. Nothing was written."
            )
        text = text.replace(old, new, 1)
        applied.append(name)
    return text, applied, skipped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load authors through paper_meta upsert")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--path", default=str(TARGET))
    args = parser.parse_args(argv)

    path = pathlib.Path(args.path)
    if not path.is_file():
        print(f"ERROR: {path} not found (run from the repo root)")
        return 2

    before = path.read_text()
    after, applied, skipped = patch(before)
    for name in applied:
        print(f"  + {name}")
    for name in skipped:
        print(f"  . {name} (already present)")
    if not applied and not skipped:
        print("nothing to do.")
        return 0

    ast.parse(after)

    if args.dry_run:
        sys.stdout.writelines(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile=f"{path} (current)",
                tofile=f"{path} (patched)",
                n=2,
            )
        )
        print("--dry-run: nothing written.")
        return 0

    if after != before:
        path.write_text(after)
        print(f"  wrote {path}")
    else:
        print("nothing to do.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
