#!/usr/bin/env python3
"""Day 6 stage 6a: SearchDB.search_author, in the same shape as search_keyword.

One anchored insertion: the new method goes immediately ahead of `def paper(...)`.
The engine import is local, so this patch touches one region of db.py rather than
also editing an import block it has never seen.

Must be applied BEFORE scripts/day6_patch_api.py: the endpoint calls this.

    python scripts/day6_patch_db.py --dry-run
    python scripts/day6_patch_db.py
"""

from __future__ import annotations

import argparse
import ast
import difflib
import pathlib
import sys

TARGET = pathlib.Path("src/docpulse/search/db.py")
APPLIED_MARKER = "def search_author("
ANCHOR = '''    def paper(self, arxiv_id: str) -> dict[str, Any] | None:'''

METHOD = '''    def search_author(
        self,
        *,
        author: str,
        limit: int,
        category: str | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> dict[str, Any]:
        """Author search, exact name first and partial only as a fallback.

        Returns the engine's dict unchanged. Unlike search_keyword there is no
        second metadata pass: the author SQL already selects title and authors
        from paper_meta, so there is nothing to merge and no filtered hit to drop.

        The engine import is local so this patch touches one region of this file.
        """
        from docpulse.search.authors import search_author as run_author_search

        with self._lock, self._cursor() as cur:
            return run_author_search(
                cur,
                author,
                limit=int(limit),
                category=(category or None) or None,
                since=(since or None) or None,
                until=(until or None) or None,
            )

'''


def patch(text: str) -> tuple[str, list[str]]:
    if APPLIED_MARKER in text:
        return text, ["already applied; nothing to do"]
    hits = text.count(ANCHOR)
    if hits != 1:
        raise SystemExit(
            f"ERROR: anchor 'def paper(' appears {hits} times, expected exactly 1.\n"
            "Refusing to guess. Nothing was written."
        )
    return text.replace(ANCHOR, METHOD + ANCHOR, 1), ["SearchDB.search_author inserted"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Add SearchDB.search_author")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--path", default=str(TARGET))
    args = parser.parse_args(argv)

    path = pathlib.Path(args.path)
    if not path.is_file():
        print(f"ERROR: {path} not found (run from the repo root)")
        return 2

    before = path.read_text()
    after, done = patch(before)
    for name in done:
        print(f"  {name}")
    if done[0].startswith("already"):
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
    print(f"  wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
