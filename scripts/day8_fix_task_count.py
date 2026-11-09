#!/usr/bin/env python3
"""One wrong number in EVIDENCE.md: the DAG has seven tasks, not six.

`airflow tasks list arxiv_bronze_ingest` prints seven: build_silver, check_meta,
ensure_bucket, ingest_to_bronze, load_meta, verify_bronze, verify_silver. Counting the
chain by hand gives the same seven. The row said six because it was written from memory
instead of from that command.

    python3 scripts/day8_fix_task_count.py --dry-run
    python3 scripts/day8_fix_task_count.py
"""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

TARGET = Path("EVIDENCE.md")

OLD = (
    "| DAG shape | **6 tasks**, ends at `check_meta`; embeddings run as `make embed` "
    "in the image that owns the model | `dags/arxiv_bronze_ingest.py` | **Verified** |"
)
NEW = (
    "| DAG shape | **7 tasks** (`airflow tasks list`), ends at `check_meta`; embeddings "
    "run as `make embed` in the image that owns the model | `airflow tasks list` "
    "| **Verified** |"
)
MARKER = "**7 tasks** (`airflow tasks list`)"

# A scheduled run that completed after the fix, with no human in the loop.
RUN_OLD = "| What was already automated |"
RUN_ROW = (
    "| Scheduled run after the fix | `scheduled__2026-11-08T00:00:00` **success**, "
    "created and executed by the scheduler at 19:44:27 → 19:46:05 UTC with no human "
    "action beyond unpausing | `dags list-runs` | **Verified** |\n"
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="fix the task count in EVIDENCE.md")
    parser.add_argument("--path", default=str(TARGET))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    path = Path(args.path)
    if not path.is_file():
        raise SystemExit(f"{path} not found — run this from the repo root")

    before = path.read_text(encoding="utf-8")
    after = before

    if MARKER in after:
        print("  . task count: already applied")
    else:
        hits = after.count(OLD)
        if hits != 1:
            raise SystemExit(
                f"DAG-shape row matched {hits} times, expected exactly 1. "
                "EVIDENCE.md has changed; nothing was written."
            )
        after = after.replace(OLD, NEW, 1)
        print("  + task count: six → seven, source now `airflow tasks list`")

    if "Scheduled run after the fix" in after:
        print("  . scheduled-run row: already applied")
    else:
        i = after.find(RUN_OLD)
        if i == -1:
            raise SystemExit("'What was already automated' row not found; nothing was written.")
        after = after[:i] + RUN_ROW + after[i:]
        print("  + scheduled-run row: first fully unattended success")

    if after == before:
        print("nothing to do")
        return 0

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

    path.write_text(after, encoding="utf-8")
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
