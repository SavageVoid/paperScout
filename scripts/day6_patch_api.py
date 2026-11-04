#!/usr/bin/env python3
"""Day 6 stage 6: give /search an author arm.

Four anchored replacements. Each anchor must appear exactly once or nothing is
written; re-running is a no-op; --dry-run diffs first.

  1. q becomes optional, and `author` joins the signature
  2. the guard accepts either parameter -- 422 only when both are absent
  3. an author branch ahead of the keyword branch, reporting mode="author"
  4. the author diagnostics join the response

Apply scripts/day6_patch_db.py FIRST: this calls db.search_author(), which does
not exist until that patch lands.

    python scripts/day6_patch_api.py --dry-run
    python scripts/day6_patch_api.py
"""

from __future__ import annotations

import argparse
import ast
import difflib
import pathlib
import sys

TARGET = pathlib.Path("src/docpulse/search/api.py")
APPLIED_MARKER = 'author: str | None = Query(None'

PARAMS_OLD = '''        q: str = Query(..., min_length=MIN_QUERY_CHARS, max_length=MAX_QUERY_CHARS),'''

PARAMS_NEW = '''        q: str | None = Query(None, min_length=MIN_QUERY_CHARS, max_length=MAX_QUERY_CHARS),
        author: str | None = Query(None, max_length=200, description="author name"),'''

GUARD_OLD = '''        text = normalise_query(q)
        if not text:
            raise HTTPException(status_code=422, detail="query is empty after trimming")'''

GUARD_NEW = '''        text = normalise_query(q or "")
        wanted_author = " ".join((author or "").split())
        if not text and not wanted_author:
            # Neither was given, so there is nothing to search. A blank q with a
            # real author is fine: the author arm never looks at q.
            raise HTTPException(
                status_code=422, detail="q or author is required (q cannot be blank)"
            )'''

BRANCH_OLD = '''        if mode == "keyword":'''

BRANCH_NEW = '''        author_info: dict[str, Any] | None = None

        if wanted_author:
            # A separate arm: "this person is in the author list" is a stronger
            # claim than "these words appear", and the two must not be blended.
            try:
                found = db.search_author(
                    author=wanted_author,
                    limit=clamped,
                    category=category,
                    since=since,
                    until=until,
                )
            except Exception as exc:
                raise HTTPException(
                    status_code=503, detail=f"database unavailable: {exc}"
                ) from exc
            rows = found["rows"]
            mode = "author"
            author_info = {
                "mode": found["mode"],
                "key": found["key"],
                "total_matches": found["total_matches"],
            }
        elif mode == "keyword":'''

ECHO_OLD = '''            "min_score": min_score,
            "keyword": keyword_info,'''

ECHO_NEW = '''            "min_score": min_score,
            "keyword": keyword_info,
            "author": author_info,'''

EDITS = (
    ("q optional, author added", PARAMS_OLD, PARAMS_NEW),
    ("guard accepts either parameter", GUARD_OLD, GUARD_NEW),
    ("author branch ahead of keyword", BRANCH_OLD, BRANCH_NEW),
    ("author diagnostics in the response", ECHO_OLD, ECHO_NEW),
)


def patch(text: str) -> tuple[str, list[str]]:
    if APPLIED_MARKER in text:
        return text, ["already applied; nothing to do"]

    for name, anchor, _new in EDITS:
        hits = text.count(anchor)
        if hits != 1:
            raise SystemExit(
                f"ERROR: anchor for {name!r} appears {hits} times, expected exactly 1.\n"
                "Refusing to guess. Nothing was written."
            )

    done = []
    for name, anchor, new in EDITS:
        text = text.replace(anchor, new, 1)
        done.append(name)
    return text, done


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Add author= to /search")
    parser.add_argument("--dry-run", action="store_true", help="print a diff, write nothing")
    parser.add_argument("--path", default=str(TARGET), help="target file")
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
