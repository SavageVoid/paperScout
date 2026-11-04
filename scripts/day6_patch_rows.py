#!/usr/bin/env python3
"""Day 6 stage 7: put `authors` on every /search row, not just author-mode rows.

Three things had to line up and only one of them was missing at each layer:

  1. the column is populated             (done in the backfill)
  2. each SQL selects it                 (four edits below)
  3. rows_to_results keeps it            (one edit below)

The author arm already selected m.authors; the vector and keyword arms never did.
Even then the value was dropped, because rows_to_results builds an explicit
whitelist dict rather than passing the row through -- so the column, the SQL and
the response shaper all had to be fixed before a single response showed an author.

Each edit is applied independently and skipped if already present, so re-running
this on a half-patched file finishes the job instead of doing nothing.

    python3 scripts/day6_patch_rows.py --dry-run
    python3 scripts/day6_patch_rows.py
"""

from __future__ import annotations

import argparse
import ast
import difflib
import pathlib
import sys

TARGET = pathlib.Path("src/docpulse/search/db.py")

SEARCH_OLD = '''       m.categories,
       m.published,
       m.updated
  FROM {table} e'''

SEARCH_NEW = '''       m.categories,
       m.published,
       m.updated,
       m.authors
  FROM {table} e'''

KEYWORD_OLD = '''SELECT m.arxiv_id, m.title, m.abstract, m.categories, m.published, m.updated,
       m.version
  FROM {meta} m'''

KEYWORD_NEW = '''SELECT m.arxiv_id, m.title, m.abstract, m.categories, m.published, m.updated,
       m.version, m.authors
  FROM {meta} m'''

PAPER_OLD = '''SELECT m.arxiv_id, m.title, m.abstract, m.categories, m.published, m.updated,
       e.version, e.model_fp, (e.embedding IS NOT NULL) AS has_vector'''

PAPER_NEW = '''SELECT m.arxiv_id, m.title, m.abstract, m.categories, m.published, m.updated,
       m.authors,
       e.version, e.model_fp, (e.embedding IS NOT NULL) AS has_vector'''

RESULT_OLD = '''            "version": row.get("version"),
            "score": round(float(row["score"]), 6),'''

RESULT_NEW = '''            "version": row.get("version"),
            "authors": row.get("authors") or [],
            "score": round(float(row["score"]), 6),'''

EDITS = (
    ("vector SQL selects m.authors", SEARCH_OLD, SEARCH_NEW),
    ("keyword SQL selects m.authors", KEYWORD_OLD, KEYWORD_NEW),
    ("paper SQL selects m.authors", PAPER_OLD, PAPER_NEW),
    ("rows_to_results keeps authors", RESULT_OLD, RESULT_NEW),
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
    parser = argparse.ArgumentParser(description="Add authors to every /search row")
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

    if not applied:
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

    path.write_text(after)
    print(f"  wrote {path} ({len(applied)} edit(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
