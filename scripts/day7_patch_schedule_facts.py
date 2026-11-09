#!/usr/bin/env python3
"""Correct three facts in EVIDENCE.md section 8 that the DAG source disproved.

The ledger said "06:00 daily". The DAG says `schedule="@daily"`, which is 00:00 UTC,
with `start_date` 2026-10-01, `catchup=False` and `max_active_runs=1`. Two rows are
therefore wrong and one row is missing: the `dags list-runs` table shows scheduled runs
on 29, 30 and 31 October that all succeeded while the chain still ended at
`verify_silver` — the exact gap this day closed.

    python3 scripts/day7_patch_schedule_facts.py --dry-run
    python3 scripts/day7_patch_schedule_facts.py
"""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

TARGET = Path("EVIDENCE.md")

EDITS: list[tuple[str, str, str, str]] = [
    (
        "schedule row",
        "| Schedule | 06:00 daily, `is_paused False`, one DAG `arxiv_bronze_ingest` "
        "| `airflow dags list` | **Verified** |",
        "| Schedule | `@daily` (**00:00 UTC**), `start_date` 2026-10-01, `catchup=False`, "
        "`max_active_runs=1`, two retries with exponential backoff, `is_paused False` "
        "| `dags next-execution` + DAG source | **Verified** |",
        "`@daily` (**00:00 UTC**)",
    ),
    (
        "gate row title",
        "| Gate at 06:00 | ran in isolation with no upstream XCom and still passed the "
        "four integrity counts | same task test | **Verified** |",
        "| Gate in isolation | ran with no upstream XCom and still passed the four "
        "integrity counts | `tasks test` | **Verified** |",
        "| Gate in isolation |",
    ),
]

INSERT_AFTER = (
    "| DB driver | one `_connect` tries psycopg 3, falls back to psycopg2, "
    "because the Airflow image ships only the latter | `src/docpulse/silver/load.py` "
    "| **Verified** |"
)
INSERT_ROW = (
    INSERT_AFTER
    + "\n| What was already automated | `scheduled__2026-10-29`, `10-30`, `10-31` all "
    "**success** — but the chain ended at `verify_silver`, one layer short of the "
    "database. This is the gap Day 7 closed, not a claim that scheduling was new "
    "| `dags list-runs` | **Verified** |"
)
INSERT_MARKER = "What was already automated"


def replace_once(text: str, label: str, old: str, new: str, marker: str) -> tuple[str, str]:
    if marker in text:
        return text, f"  . {label}: already applied"
    hits = text.count(old)
    if hits != 1:
        raise SystemExit(
            f"anchor for '{label}' matched {hits} times, expected exactly 1. "
            "EVIDENCE.md has changed; nothing was written."
        )
    return text.replace(old, new, 1), f"  + {label}"


def patch(text: str) -> tuple[str, list[str]]:
    notes = []
    for label, old, new, marker in EDITS:
        text, note = replace_once(text, label, old, new, marker)
        notes.append(note)
    text, note = replace_once(text, "scheduled-runs row", INSERT_AFTER, INSERT_ROW, INSERT_MARKER)
    notes.append(note)
    return text, notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="fix schedule facts in EVIDENCE.md")
    parser.add_argument("--path", default=str(TARGET))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    path = Path(args.path)
    if not path.is_file():
        raise SystemExit(f"{path} not found — run this from the repo root")

    before = path.read_text(encoding="utf-8")
    after, notes = patch(before)
    for note in notes:
        print(note)

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
