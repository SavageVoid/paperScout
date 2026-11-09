#!/usr/bin/env python3
"""Record the unattended embedding loop, and retire a claim that is no longer true.

The ledger currently says embeddings "stay a deliberate `make embed`". As of
2026-11-09 19:51 that is false: a cron entry runs `scripts/embed_new.sh` every 30
minutes, and on its first invocation it embedded 690 rows (663 new, 27 changed) with
0 failures in 52.6 seconds, then no-opped on the second.

EVIDENCE.md edits are strict — a missing anchor aborts and writes nothing.
README.md edits are tolerant — if README.md is not the Day-7 rewrite, they are skipped
with a note instead of blocking the ledger.

    python3 scripts/day8_patch_evidence_automation.py --dry-run
    python3 scripts/day8_patch_evidence_automation.py
"""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

EVIDENCE = Path("EVIDENCE.md")
README = Path("README.md")

CORPUS_OLD = "| Papers | **8,274** (was 4,271 before the 6 Nov scheduled run) | `select count(*) from paper_meta` | **Verified** |"
CORPUS_NEW = "| Papers | **8,937** (4,271 → 8,274 → 8,937 across three scheduled passes) | `select count(*) from paper_meta` | **Verified** |"

ANCHOR_ROW = (
    "| Fingerprint moved, correctly | `b1333bbebda572f7…` at 4,271 rows → "
    "`c2337d98dce34d6c…` at 8,274; it tracks corpus content, so growth **should** "
    "change it | `authors_fp` across loads | **Verified** |"
)
NEW_ROWS = (
    ANCHOR_ROW
    + "\n| Vectors without a human | cron runs `scripts/embed_new.sh` every 30 minutes; "
    "it compares `paper_meta` to `paper_embeddings` and exits in milliseconds when they "
    "are equal | `logs/embed_new.log` | **Verified** |"
    + "\n| Unattended embed on new papers | `n_new 663, n_changed 27, n_to_embed 690, "
    "n_embedded 690, n_skipped 8,247`, 1,030,288 chars, **52.653 s**, run "
    "`embed-442c2aaf6a5a`, `n_failed 0` | `embedding_runs` | **Verified** |"
    + "\n| Timer is idempotent | second invocation printed `papers=8937 vectors=8937` then "
    "`nothing new: every paper already has a vector` | `scripts/embed_new.sh` "
    "| **Verified** |"
    + "\n| Loop closed | `papers=8937 vectors=8937` — the scheduled run added 663 papers, "
    "the timer vectorised them two minutes later, with no human action | count query "
    "| **Verified** |"
)

PARA_OLD = """What is *not* automated, stated plainly: the DAG does not embed. `build_encoder()`
refuses to run without `sentence-transformers` rather than write meaningless vectors,
so the embedding step stays a deliberate `make embed` in the embedder image. The DAG
guarantees every `paper_meta` row is present and gated; it does not guarantee a vector."""
PARA_NEW = """Where embeddings run, and why not in the DAG: `build_encoder()` refuses to run
without `sentence-transformers` rather than write meaningless vectors, and the Airflow
image has no torch, so the step stays out of the DAG. It is automated anyway. A host
cron entry runs `scripts/embed_new.sh` every 30 minutes, which compares `paper_meta`
to `paper_embeddings`, exits in milliseconds when they match, and otherwise embeds in the
image that owns the model. The DAG guarantees every `paper_meta` row is present and
gated; the timer guarantees each one receives a vector."""

EVIDENCE_EDITS = [
    ("corpus count", CORPUS_OLD, CORPUS_NEW, "**8,937**"),
    ("automation rows", ANCHOR_ROW, NEW_ROWS, "Vectors without a human"),
    ("retire the manual-embed claim", PARA_OLD, PARA_NEW, "the timer guarantees each one"),
]

README_EDITS = [
    ("papers", "| papers | **8,274** |", "| papers | **8,937** |", "| papers | **8,937** |"),
    (
        "embeddings",
        "| embeddings | **8,274** — one vector per paper, `(8274, 8274)` |",
        "| embeddings | **8,937** — one vector per paper, `(8937, 8937)` |",
        "| embeddings | **8,937**",
    ),
    (
        "silver rows",
        "| silver | 8,274 rows across **60 months**, 0 quarantined |",
        "| silver | 8,937 rows across **60 months**, 0 quarantined |",
        "| silver | 8,937 rows",
    ),
    (
        "health expectation",
        'Expect `ok: true`, `db: "up"`, `n_vectors: 8274`.',
        'Expect `ok: true`, `db: "up"`, `n_vectors: 8937`.',
        "`n_vectors: 8937`",
    ),
    (
        "timer row",
        "| incremental embed | **4,068** embedded, **4,206** skipped, **0** failed, 300 s |",
        "| incremental embed | **4,068** embedded, **4,206** skipped, **0** failed, 300 s |\n"
        "| unattended embed | **690** embedded (663 new, 27 changed), 0 failed, **52.7 s**, "
        "triggered by cron |",
        "| unattended embed |",
    ),
]


def apply_edits(text: str, edits: list, strict: bool, name: str) -> tuple[str, list[str]]:
    notes = []
    for label, old, new, marker in edits:
        if marker in text:
            notes.append(f"  . {name} {label}: already applied")
            continue
        hits = text.count(old)
        if hits != 1:
            if strict:
                raise SystemExit(
                    f"{name}: anchor for '{label}' matched {hits} times, expected exactly 1. "
                    "Nothing was written."
                )
            notes.append(f"  ! {name} {label}: anchor not found once, skipped")
            continue
        text = text.replace(old, new, 1)
        notes.append(f"  + {name} {label}")
    return text, notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="record the unattended embedding loop")
    parser.add_argument("--evidence", default=str(EVIDENCE))
    parser.add_argument("--readme", default=str(README))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    ev_path = Path(args.evidence)
    if not ev_path.is_file():
        raise SystemExit(f"{ev_path} not found — run this from the repo root")

    ev_before = ev_path.read_text(encoding="utf-8")
    ev_after, ev_notes = apply_edits(ev_before, EVIDENCE_EDITS, True, "EVIDENCE.md")

    rm_path = Path(args.readme)
    rm_before = rm_after = ""
    rm_notes: list[str] = []
    if rm_path.is_file():
        rm_before = rm_path.read_text(encoding="utf-8")
        rm_after, rm_notes = apply_edits(rm_before, README_EDITS, False, "README.md")

    for note in ev_notes + rm_notes:
        print(note)

    if ev_after == ev_before and rm_after == rm_before:
        print("nothing to do")
        return 0

    if args.dry_run:
        for before, after, path in ((ev_before, ev_after, ev_path), (rm_before, rm_after, rm_path)):
            if before != after:
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

    if ev_after != ev_before:
        ev_path.write_text(ev_after, encoding="utf-8")
        print(f"wrote {ev_path}")
    if rm_after != rm_before:
        rm_path.write_text(rm_after, encoding="utf-8")
        print(f"wrote {rm_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
