#!/usr/bin/env python3
"""Day 7 stage 0a: make /openapi.json (and therefore /docs) work again.

`def health() -> JSONResponse:` makes FastAPI treat the return annotation as a
response_model, so Pydantic is asked to build a schema for JSONResponse itself and
raises PydanticUserError. Schema generation fails lazily, on the first request for
/openapi.json, which is why the app starts cleanly and only the docs endpoints 500.

Dropping the annotation is the whole fix; the function still returns JSONResponse.
Routes with concrete return types are left alone.

    python3 scripts/day7_patch_health.py --dry-run
    python3 scripts/day7_patch_health.py
"""

from __future__ import annotations

import argparse
import ast
import difflib
import pathlib
import sys

TARGET = pathlib.Path("src/docpulse/search/api.py")

OLD = "    def health() -> JSONResponse:"
NEW = "    def health():"


def patch(text: str) -> tuple[str, str]:
    if NEW in text and OLD not in text:
        return text, "already applied; nothing to do"
    hits = text.count(OLD)
    if hits != 1:
        raise SystemExit(
            f"ERROR: anchor appears {hits} times, expected exactly 1. Nothing was written.\n"
            f"Looked for: {OLD!r}"
        )
    return text.replace(OLD, NEW, 1), "health() no longer annotated with JSONResponse"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Unbreak /openapi.json")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--path", default=str(TARGET))
    args = parser.parse_args(argv)

    path = pathlib.Path(args.path)
    if not path.is_file():
        print(f"ERROR: {path} not found (run from the repo root)")
        return 2

    before = path.read_text()
    after, note = patch(before)
    print(f"  {note}")
    if after == before:
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
