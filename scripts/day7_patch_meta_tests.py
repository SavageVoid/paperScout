#!/usr/bin/env python3
"""Day 7 stage 4: point test_search_meta at the module that now owns the code.

Day 4 loaded scripts/day4_load_meta.py by path, which was correct while the script
held the implementation. Day 7 promoted the mapping into docpulse.silver.load (the
DAG calls it too), so the script is a CLI and the tests were reaching into a façade:
12 failures, all AttributeError, none of them a behaviour change.

The assertions are untouched. Only the import changes.

    python3 scripts/day7_patch_meta_tests.py --dry-run
    python3 scripts/day7_patch_meta_tests.py
"""

from __future__ import annotations

import argparse
import ast
import difflib
import pathlib
import sys

TESTS = pathlib.Path("tests/test_search_meta.py")

DOC_OLD = """The script is loaded by path because it is a CLI, not an importable module; the
functions it exposes are pure, which is the point.
\"\"\""""

DOC_NEW = """The mapping lives in ``docpulse.silver.load`` as of Day 7: the DAG calls it, the
CLI script calls it, and these tests call it. When it lived in the script, the two
copies drifted -- the script folded arXiv's markup out of author names and the
promoted path did not -- so there is now one implementation and one target for tests.
\"\"\""""

HEADER_OLD = '''import importlib.util
import pathlib
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "day4_load_meta.py"

_spec = importlib.util.spec_from_file_location("day4_load_meta_under_test", SCRIPT)
assert _spec and _spec.loader
meta = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(meta)
'''

HEADER_NEW = '''import unittest

from docpulse.silver import load as meta
'''

EDITS = (
    ("the docstring says where the code lives now", DOC_OLD, DOC_NEW),
    ("the module under test is docpulse.silver.load", HEADER_OLD, HEADER_NEW),
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Retarget the paper_meta tests")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--path", default=str(TESTS))
    args = parser.parse_args(argv)

    path = pathlib.Path(args.path)
    if not path.is_file():
        print(f"ERROR: {path} not found (run from the repo root)")
        return 2

    before = path.read_text()
    after = before
    applied, skipped = [], []
    for name, old, new in EDITS:
        if new in after:
            skipped.append(name)
            continue
        hits = after.count(old)
        if hits != 1:
            raise SystemExit(
                f"ERROR: anchor for {name!r} appears {hits} times, expected exactly 1.\n"
                "Refusing to guess. Nothing was written."
            )
        after = after.replace(old, new, 1)
        applied.append(name)

    for name in applied:
        print(f"  + {name}")
    for name in skipped:
        print(f"  . {name} (already present)")
    if not applied:
        print("nothing to do.")
        return 0

    ast.parse(after)

    if args.dry_run:
        sys.stdout.writelines(difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"{path} (current)",
            tofile=f"{path} (patched)",
            n=2,
        ))
        print("--dry-run: nothing written.")
        return 0

    path.write_text(after)
    print(f"  wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
