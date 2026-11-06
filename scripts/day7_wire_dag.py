#!/usr/bin/env python3
"""Day 7 stage 3: wire the load and the gate into the DAG (audit I-01).

The DAG stopped at verify_silver. Bronze and silver ran on a schedule; the hop that
makes a paper searchable was a command a human typed. So a new announcement landed
in silver and stopped there, and nothing failed.

Two edits:

  * silver/tasks.py gains run_load_meta and run_check_meta, in the same idiom as
    run_build_silver: injectable dependencies, Airflow-free, no new convention.
  * the DAG gains four tasks and one wiring expression, ending at verify_embeddings.

MetaIntegrityError is translated to AirflowFailException at the DAG layer, exactly as
SilverError already is, so a degraded corpus turns the run red at the load.

    python3 scripts/day7_wire_dag.py --dry-run
    python3 scripts/day7_wire_dag.py
"""

from __future__ import annotations

import argparse
import ast
import difflib
import pathlib
import sys

TASKS = pathlib.Path("src/docpulse/silver/tasks.py")
DAG = pathlib.Path("dags/arxiv_bronze_ingest.py")

TASKS_OLD = '''    if summary is not None and summary.get("n_papers_total") != verified["n_papers_total"]:
        raise RuntimeError(f"verified silver differs from build summary: {verified} vs {summary}")
    return verified
'''

TASKS_NEW = '''    if summary is not None and summary.get("n_papers_total") != verified["n_papers_total"]:
        raise RuntimeError(f"verified silver differs from build summary: {verified} vs {summary}")
    return verified


def run_load_meta(
    *,
    store: ObjectStore | None = None,
    connect: Any = None,
    batch: int | None = None,
    meta_table: str | None = None,
) -> dict[str, Any]:
    """Silver -> paper_meta. Upsert only: pruning stays a human decision."""
    from docpulse.silver.load import (
        DEFAULT_BATCH,
        META_TABLE,
        build_rows_from_silver,
        load_meta,
        read_silver_rows,
    )

    raw_rows, facts = read_silver_rows(store=store or _store())
    rows = build_rows_from_silver(raw_rows)
    report = load_meta(
        rows,
        connect=connect,
        batch=batch or DEFAULT_BATCH,
        meta_table=meta_table or META_TABLE,
    )
    return report | facts | {"n_unique_papers": len(rows)}


def run_check_meta(
    summary: dict[str, Any] | None = None,
    *,
    connect: Any = None,
    meta_table: str | None = None,
) -> dict[str, Any]:
    """The gate: raises MetaIntegrityError when author search would start lying."""
    from docpulse.silver.load import META_TABLE, check_meta

    report = check_meta(connect=connect, meta_table=meta_table or META_TABLE)
    written = (summary or {}).get("n_unique_papers")
    if written is not None and written != report["papers"]:
        raise RuntimeError(
            f"paper_meta holds {report['papers']} papers but the load wrote {written}; "
            "the load and the table disagree"
        )
    return report
'''

DAG_OLD = '''    @task
    def verify_silver(summary: dict) -> dict:
        from docpulse.silver.tasks import run_verify_silver

        return run_verify_silver(summary)

    verify_silver(build_silver(verify_bronze(ingest_to_bronze(ensure_bucket()))))
'''

DAG_NEW = '''    @task
    def verify_silver(summary: dict) -> dict:
        from docpulse.silver.tasks import run_verify_silver

        return run_verify_silver(summary)

    @task
    def load_meta(_verified_silver: dict) -> dict:
        from docpulse.silver.load import MetaIntegrityError
        from docpulse.silver.tasks import run_load_meta

        try:
            return run_load_meta()
        except MetaIntegrityError as exc:
            # a corpus that cannot be searched is a deterministic failure, not a retry
            raise AirflowFailException(str(exc)) from exc

    @task
    def check_meta(summary: dict) -> dict:
        from docpulse.silver.load import MetaIntegrityError
        from docpulse.silver.tasks import run_check_meta

        try:
            return run_check_meta(summary)
        except MetaIntegrityError as exc:
            raise AirflowFailException(str(exc)) from exc

    @task(execution_timeout=timedelta(minutes=60))
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

EDITS = (
    ("tasks.py gains run_load_meta and run_check_meta", TASKS, TASKS_OLD, TASKS_NEW),
    ("the DAG loads, gates, embeds and verifies embeddings", DAG, DAG_OLD, DAG_NEW),
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Wire the load step into the DAG")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--tasks", default=str(TASKS))
    parser.add_argument("--dag", default=str(DAG))
    args = parser.parse_args(argv)

    targets = {TASKS: pathlib.Path(args.tasks), DAG: pathlib.Path(args.dag)}
    for path in targets.values():
        if not path.is_file():
            print(f"ERROR: {path} not found (run from the repo root)")
            return 2

    before = {p: targets[p].read_text() for p in targets}
    after = dict(before)
    applied, skipped = [], []
    for name, which, old, new in EDITS:
        text = after[which]
        if new in text:
            skipped.append(name)
            continue
        hits = text.count(old)
        if hits != 1:
            raise SystemExit(
                f"ERROR: anchor for {name!r} appears {hits} times in {which}, expected 1.\n"
                "Refusing to guess. Nothing was written."
            )
        after[which] = text.replace(old, new, 1)
        applied.append(name)

    for name in applied:
        print(f"  + {name}")
    for name in skipped:
        print(f"  . {name} (already present)")
    if not applied:
        print("nothing to do.")
        return 0

    for text in after.values():
        ast.parse(text)

    if args.dry_run:
        for which in targets:
            if before[which] == after[which]:
                continue
            sys.stdout.writelines(difflib.unified_diff(
                before[which].splitlines(keepends=True),
                after[which].splitlines(keepends=True),
                fromfile=f"{targets[which]} (current)",
                tofile=f"{targets[which]} (patched)",
                n=2,
            ))
        print("--dry-run: nothing written.")
        return 0

    for which in targets:
        if before[which] != after[which]:
            targets[which].write_text(after[which])
            print(f"  wrote {targets[which]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
