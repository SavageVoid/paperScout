#!/usr/bin/env python3
"""Day 5 step 3c: add keyword search to SearchDB, by insertion, idempotent.

Three edits, each anchored on a line that appears exactly once in the real file:

  1. an import of the keyword engine and the policy helpers
  2. KEYWORD_META_SQL, next to the other statements
  3. SearchDB.search_keyword, just before SearchDB.paper

Anchors are asserted rather than trusted: if the file has drifted, the script stops
before writing anything. Re-running it is a no-op. --dry-run prints a unified diff.

    python scripts/day5_patch_db.py --dry-run
    python scripts/day5_patch_db.py
"""

from __future__ import annotations

import argparse
import ast
import difflib
import pathlib
import sys

TARGET = pathlib.Path("src/docpulse/search/db.py")

ANCHOR_IMPORTS = "from typing import Any, Iterable, Sequence"
ANCHOR_SQL = 'COUNT_SQL = "SELECT count(*) FROM {meta}"'
ANCHOR_METHOD = "    def paper(self, arxiv_id: str) -> dict[str, Any] | None:"

IMPORTS = """from docpulse.search.keyword import search_keyword as run_keyword_search
from docpulse.search.modes import keyword_report, merge_keyword_rows
"""

SQL = '''KEYWORD_META_SQL = """
SELECT m.arxiv_id, m.title, m.abstract, m.categories, m.published, m.updated,
       m.version
  FROM {meta} m
 WHERE m.arxiv_id = ANY(%(ids)s::text[])
   AND (%(category)s::text IS NULL
        OR %(category)s::text = ANY(string_to_array(coalesce(m.categories, ''), ' ')))
   AND (%(since)s::timestamptz IS NULL OR m.published >= %(since)s::timestamptz)
   AND (%(until)s::timestamptz IS NULL OR m.published <  %(until)s::timestamptz)
"""
'''

METHOD = '''    def search_keyword(
        self,
        *,
        query: str,
        limit: int,
        category: str | None = None,
        since: str | None = None,
        until: str | None = None,
        sort: str = "relevance",
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Full-text search over paper_meta.fts; returns rows plus diagnostics.

        Rows come back shaped like search()'s, so the API formats both modes with
        one function. The diagnostics say whether the AND arm answered or the OR
        fallback did: an exact match must not look like a near match.

        The keyword engine filters on category; the metadata lookup repeats that
        and adds the date range, which is where a filtered hit is dropped.
        """
        with self._lock, self._cursor() as cur:
            found = run_keyword_search(
                cur,
                query,
                limit=int(limit),
                category=(category or None) or None,
                sort=sort,
            )
            if not found["rows"]:
                return [], keyword_report(found, [])
            sql = KEYWORD_META_SQL.format(meta=self.meta_table)
            params = {
                "ids": [hit["arxiv_id"] for hit in found["rows"]],
                "category": (category or None) or None,
                "since": (since or None) or None,
                "until": (until or None) or None,
            }
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            meta = {r[0]: dict(zip(cols, r, strict=True)) for r in cur.fetchall()}
        rows = merge_keyword_rows(found["rows"], meta)
        return rows, keyword_report(found, rows)

'''


def patch(text: str) -> tuple[str, list[str]]:
    """Apply the three insertions. Returns the new text and what was done."""
    done: list[str] = []

    if "run_keyword_search" in text:
        return text, ["already applied; nothing to do"]

    for name, anchor in (
        ("imports", ANCHOR_IMPORTS),
        ("sql", ANCHOR_SQL),
        ("method", ANCHOR_METHOD),
    ):
        hits = text.count(anchor)
        if hits != 1:
            raise SystemExit(
                f"ERROR: anchor {name!r} appears {hits} times, expected exactly 1:\n"
                f"  {anchor!r}\n"
                "Refusing to guess. Nothing was written."
            )

    text = text.replace(ANCHOR_IMPORTS, ANCHOR_IMPORTS + "\n" + IMPORTS.rstrip("\n"), 1)
    done.append("import added")

    text = text.replace(ANCHOR_SQL, ANCHOR_SQL + "\n\n" + SQL.rstrip("\n"), 1)
    done.append("KEYWORD_META_SQL added")

    text = text.replace(ANCHOR_METHOD, METHOD + ANCHOR_METHOD, 1)
    done.append("SearchDB.search_keyword added")

    return text, done


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Insert keyword search into db.py")
    parser.add_argument("--dry-run", action="store_true", help="print a diff, write nothing")
    parser.add_argument("--path", default=str(TARGET), help="target file")
    args = parser.parse_args(argv)

    path = pathlib.Path(args.path)
    if not path.is_file():
        print(f"ERROR: {path} not found (run from the repo root)")
        return 2

    before = path.read_text()
    after, done = patch(before)

    for line in done:
        print(f"  {line}")
    if done and done[0].startswith("already"):
        return 0

    ast.parse(after)  # the file must be valid Python before it is written

    if args.dry_run:
        diff = difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"{path} (current)",
            tofile=f"{path} (patched)",
            n=2,
        )
        sys.stdout.writelines(diff)
        print("--dry-run: nothing written.")
        return 0

    path.write_text(after)
    print(f"  wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
