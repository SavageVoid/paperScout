#!/usr/bin/env python3
"""Day 9: put the retrieval work into the evidence ledger.

Seven rows covering the model swap, the guard that made it safe, the truncation defect,
the author contract, the query-instruction null result, the tiered retrieval numbers and
the two probes that decide what to build next.

Anchored on the last row of section 8 and idempotent. A missing anchor writes nothing.

    python3 scripts/day9_patch_evidence_retrieval.py --dry-run
    python3 scripts/day9_patch_evidence_retrieval.py
"""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

TARGET = Path("EVIDENCE.md")

ANCHOR = (
    "| Loop closed | `papers=8937 vectors=8937` — the scheduled run added 663 papers, "
    "the timer vectorised them two minutes later, with no human action | count query "
    "| **Verified** |"
)

ROWS = (
    ANCHOR
    + "\n| Model swap | 8,937 rows re-embedded with `BAAI/bge-small-en-v1.5` @ "
    "`5c38ec7c405ec4b44b94cc5a9bb96e735b38267a`; `n_dropped_for_model_change 8937`, new "
    "`model_fp 7e978d0139627414`, 13,610,365 chars, **2,433.652 s**, `n_failed 0`, run "
    "`embed-06b3da897fc7` | `embedding_runs` | **Verified** |"
    + "\n| Model-change guard worked | build logged `model change accepted; old spaces "
    "dropped: ['2d51d6d165b7d00e']`, and `/health` returned **503** until the re-embed "
    "finished, so no query ever mixed two vector spaces | `warnings[]`, `/health` "
    "| **Verified** |"
    + "\n| Silent truncation, found | model window **256 tokens** while `EMBED_MAX_CHARS=4000** "
    "permits 4,000 chars; mean embedded text 1,493 chars, so **~33% of every abstract never "
    "entered its vector** and nothing warned | `max_seq_length` probe | **Verified** |"
    + "\n| Author contract | `mode=author` is rejected: **422** `mode must be one of vector, "
    "keyword`; author mode is inferred from `author=`. A **full** first-author name gives "
    "recall@10 **1.000** / MRR 0.913 at 3.3 ms; a bare surname returns other people | curl "
    "probes + `EVAL.md` | **Verified** |"
    + "\n| Query instruction | bge instruction applied to queries only, visible as "
    "`query_prefix` in `/health`; paraphrase results unchanged within noise, keyword rows "
    "byte-identical (the control) | `EVAL_PARAPHRASE_INSTR.md` | **Verified** |"
    + "\n| Semantic retrieval, measured | easy tier vector **0.900** recall@10 vs keyword "
    "0.600 (MRR 0.800 vs 0.420); med 0.400 vs 0.500; hard 0.200 vs 0.300 — n=10 per tier, "
    "±0.18 | `EVAL.md` | **Verified** |"
    + "\n| Retrieval probes | a missed paper sits at **rank 42** of 8,937 (rank 83 under "
    "MiniLM, so the model swap halved it); a second miss is **absent from the top 500 under "
    "both models** — rankable and unretrievable failures need different fixes | curl at limit=500 "
    "| **Verified** |"
)

MARKER = "Model-change guard worked"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="record Day 9 retrieval evidence")
    parser.add_argument("--path", default=str(TARGET))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    path = Path(args.path)
    if not path.is_file():
        raise SystemExit(f"{path} not found — run this from the repo root")

    before = path.read_text(encoding="utf-8")
    if MARKER in before:
        print("  . already applied")
        print("nothing to do")
        return 0

    hits = before.count(ANCHOR)
    if hits != 1:
        raise SystemExit(
            f"anchor matched {hits} times, expected exactly 1. EVIDENCE.md has changed; "
            "nothing was written."
        )

    after = before.replace(ANCHOR, ROWS, 1)
    print("  + seven Day-9 retrieval rows")

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
