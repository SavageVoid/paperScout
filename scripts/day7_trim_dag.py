#!/usr/bin/env python3
"""Day 7 stage 6: take the embedding tasks out of the DAG.

Decided by measurement, not taste. run_verify_embeddings calls _deps(), which calls
build_encoder(), which raises when sentence-transformers is absent:

    RuntimeError: sentence-transformers is not importable here. Run this inside the
    `embedder` container (make embed), or set EMBED_ENCODER=hash for a plumbing-only
    run. ... Doing an automatic silent fallback would put meaningless vectors in the
    table, so this is deliberately fatal.

So the Airflow image cannot verify embeddings, let alone build them. The alternative
was 1.2 GB of torch on a disk with 4.8 GB free, which is how a working box becomes a
broken one. The DAG therefore ends at check_meta -- the step that proves paper_meta is
searchable -- and embeddings stay a command in the image that owns the model.

    python3 scripts/day7_trim_dag.py --dry-run
    python3 scripts/day7_trim_dag.py
"""

from __future__ import annotations

import argparse
import ast
import difflib
import pathlib
import sys

TARGET = pathlib.Path("dags/arxiv_bronze_ingest.py")

OLD = '''    @task(execution_timeout=timedelta(minutes=60))
    def embed_papers(_checked_meta: dict) -> dict:
        from docpulse.embed.tasks import run_build_embeddings

        return run_build_embeddings()

    @task
    def verify_embeddings(summary: dict) -> dict:
        from docpulse.embed.tasks import run_verify_embeddings

        return run_verify_embeddings()

    verified_silver = verify_silver(build_silver(verify_bronze(ingest_to_bronze(ensure_bucket()))))
    verified_embeddings = verify_embeddings(
        embed_papers(check_meta(load_meta(verified_silver)))
    )
    return verified_embeddings
'''

NEW = '''    # Embeddings are deliberately not in this DAG. sentence-transformers is not in
    # this image and build_encoder() raises rather than writing meaningless vectors,
    # so the step belongs to the container that owns the model: `make embed`.
    # What this DAG guarantees is that every paper_meta row is searchable, and
    # check_meta is the task that proves it.
    verified_silver = verify_silver(build_silver(verify_bronze(ingest_to_bronze(ensure_bucket()))))
    return check_meta(load_meta(verified_silver))
'''


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="End the DAG at check_meta")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--path", default=str(TARGET))
    args = parser.parse_args(argv)

    path = pathlib.Path(args.path)
    if not path.is_file():
        print(f"ERROR: {path} not found (run from the repo root)")
        return 2

    before = path.read_text()
    if NEW in before:
        print("  . already applied; nothing to do")
        return 0
    hits = before.count(OLD)
    if hits != 1:
        raise SystemExit(
            f"ERROR: anchor appears {hits} times, expected exactly 1. Nothing was written."
        )
    after = before.replace(OLD, NEW, 1)
    ast.parse(after)
    print("  + embed_papers and verify_embeddings removed; the DAG ends at check_meta")

    if args.dry_run:
        sys.stdout.writelines(difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"{path} (current)",
            tofile=f"{path} (patched)",
            n=3,
        ))
        print("--dry-run: nothing written.")
        return 0

    path.write_text(after)
    print(f"  wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
