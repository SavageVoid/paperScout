"""Put Day 7's measurements into EVIDENCE.md.

Every number here was printed by the box on 7 November 2026. Each edit is anchored
on one exact line, must match exactly once, and is skipped if already applied, so
running this twice is safe.

    python3 scripts/day7_patch_evidence.py --dry-run
    python3 scripts/day7_patch_evidence.py
"""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

EDITIONS: list[tuple[str, str, str]] = [
    (
        "header date",
        "it. Anything not on this page is not a claim we make. Last updated 6 November 2026.",
        "it. Anything not on this page is not a claim we make. Last updated 7 November 2026.",
    ),
    (
        "silver paper count",
        "| Papers | **4,271** | `select count(*) from paper_meta` | **Verified** |",
        "| Papers | **8,274** (was 4,271 before the 6 Nov scheduled run) "
        "| `select count(*) from paper_meta` | **Verified** |",
    ),
    (
        "test count",
        "| Test suite | **417 passed, 0 skipped** | **Verified** |",
        "| Test suite | **426 passed, 1 warning**, 3.74 s | **Verified** |",
    ),
    (
        "lint scope",
        "| Lint | `ruff check src tests` clean | **Verified** |",
        "| Lint | `ruff check src scripts dags tests` clean | **Verified** |",
    ),
    (
        "corpus after growth",
        "| Corpus intact after restart | 4,271 papers, 4,271 embeddings | **Verified** |",
        "| Corpus intact after restart | 4,271 papers, 4,271 embeddings | **Verified** |\n"
        "| Corpus after the 6 Nov scheduled ingest | **8,274 papers, 8,274 embeddings** "
        "| **Verified** |",
    ),
]

SECTION_ANCHOR = "---\n\n## Claims we may **not** make yet"

SECTION_BODY = """---

## 8. Automation — from announcement to searchable row (Day 7)

The question this section answers: does a paper arXiv announces tonight end up in
`paper_meta` tomorrow, without a human typing a command? Yes, and each link in that
chain was observed.

| claim | value | source | status |
|---|---|---|---|
| Schedule | 06:00 daily, `is_paused **False**`, one DAG `arxiv_bronze_ingest` | `airflow dags list` | **Verified** |
| DAG shape | **6 tasks**, ends at `check_meta`; embeddings run as `make embed` in the image that owns the model | `dags/arxiv_bronze_ingest.py` | **Verified** |
| Scheduled ingest | **21 pages, 4,200 entries, 4,075 in window, 10,418,609 bytes**, `stop_reason reached_window_start`, `behind_schedule False` | DAG run log | **Verified** |
| Silver build | 17,200 entries → **4,003 new** papers, 196 revised, **65 text-changed**, **0 quarantined**, 60 months | `build_silver` | **Verified** |
| Load into Postgres | **8,274 rows written**, 60 months, 8,274 read | `load_meta` → `n_written 8274` | **Verified** |
| Gate | `papers 8274, null_authors 0, empty_authors 0, missing_ci 0, missing_tsv 0`, `authors_fp c2337d98dce34d6cbf4ae82dc4e7629d` | `run_check_meta`, then `tasks test … check_meta` | **Verified** |
| Gate at 06:00 | ran in isolation with no upstream XCom and still passed the four integrity counts | same task test | **Verified** |
| DB driver | one `_connect` tries psycopg 3, falls back to psycopg2, because the Airflow image ships only the latter | `src/docpulse/silver/load.py` | **Verified** |
| Incremental embedding on new data | `n_new 4,003, n_changed 65, n_unchanged 4,206, n_to_embed 4,068, n_embedded 4,068, n_failed 0`, 6,212,694 chars, **300.22 s**, `embed-83d9b303f78c` | `embedding_runs` | **Verified** |
| Two counters agree | `n_changed 65` equals silver's `n_text_changed_papers 65` — hash-based skipping, measured against a real announcement rather than a no-op rerun | `embedding_runs` vs `build_silver` | **Verified** |
| One vector per paper | `(8274, 8274)` from `paper_meta` and `paper_embeddings` | `select (…), (…)` | **Verified** |
| Fingerprint moved, correctly | `b1333bbebda572f7…` at 4,271 rows → `c2337d98dce34d6c…` at 8,274; it tracks corpus content, so growth **should** change it | `authors_fp` across loads | **Verified** |

What is *not* automated, stated plainly: the DAG does not embed. `build_encoder()`
refuses to run without `sentence-transformers` rather than write meaningless vectors,
so the embedding step stays a deliberate `make embed` in the embedder image. The DAG
guarantees every `paper_meta` row is present and gated; it does not guarantee a vector.

"""

CLAIM_ROWS_ANCHOR = (
    "| \"Pipeline success rate\" | No measured series; early runs failed for bugs now fixed |"
)
CLAIM_ROWS_NEW = (
    CLAIM_ROWS_ANCHOR
    + "\n| \"The DAG produces embeddings\" | It deliberately stops at `check_meta`; "
    "vectors come from `make embed` in the embedder image |"
)

REVERIFY_ANCHOR = (
    'dc exec -T postgres psql -U docpulse -d docpulse_embed -c "select n_embedded, '
    'n_skipped, seconds from embedding_runs order by started_at desc limit 2"'
)
REVERIFY_NEW = (
    REVERIFY_ANCHOR
    + "\ndc run --rm --entrypoint python airflow-scheduler -c \"from docpulse.silver.load "
    "import _connect; c = _connect(); cur = c.cursor(); cur.execute('select (select "
    "count(*) from paper_meta) as papers, (select count(*) from paper_embeddings) as "
    "embeddings'); print(cur.fetchone())\""
)


def _replace_once(text: str, label: str, old: str, new: str) -> tuple[str, str]:
    if new in text and old not in text:
        return text, f"  . {label}: already applied"
    hits = text.count(old)
    if hits != 1:
        raise SystemExit(
            f"anchor for '{label}' matched {hits} times, expected exactly 1 — "
            "EVIDENCE.md has changed; inspect before patching"
        )
    return text.replace(old, new, 1), f"  + {label}"


def patch(text: str) -> tuple[str, list[str]]:
    notes: list[str] = []
    for label, old, new in EDITIONS:
        text, note = _replace_once(text, label, old, new)
        notes.append(note)

    text, note = _replace_once(text, "Day 7 section", SECTION_ANCHOR, SECTION_BODY + SECTION_ANCHOR)
    notes.append(note)
    text, note = _replace_once(text, "claim table row", CLAIM_ROWS_ANCHOR, CLAIM_ROWS_NEW)
    notes.append(note)
    text, note = _replace_once(text, "re-verify command", REVERIFY_ANCHOR, REVERIFY_NEW)
    notes.append(note)
    return text, notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write Day 7 measurements into EVIDENCE.md")
    parser.add_argument("--path", default="EVIDENCE.md", help="file to patch")
    parser.add_argument("--dry-run", action="store_true", help="show the diff, write nothing")
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
            )
        )
        print("--dry-run: nothing written.")
        return 0

    path.write_text(after, encoding="utf-8")
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
