#!/usr/bin/env python3
"""Day 7 stage 5: let the load step connect from either image.

The first real DAG run failed at load_meta with ModuleNotFoundError: psycopg. The
embedder image ships psycopg 3; the Airflow image ships psycopg2, because that is
what its own metadata database needs. The code was written against the one image it
had ever been run in.

Both drivers take a DSN, %(name)s parameters and a list of dicts for executemany, so
this is a rename with a documented reason rather than a second code path. If either
driver ever diverges on these calls, the DAG run will say so loudly -- that is the
gate's job, not a comment's.

    python3 scripts/day7_patch_pg_driver.py --dry-run
    python3 scripts/day7_patch_pg_driver.py
"""

from __future__ import annotations

import argparse
import ast
import difflib
import pathlib
import sys

TARGET = pathlib.Path("src/docpulse/silver/load.py")

OLD = '''def _connect() -> Any:
    """Open a connection using the same settings the embedder already reads."""
    import psycopg

    from docpulse.embed.config import EmbeddingSettings

    return psycopg.connect(EmbeddingSettings.from_env().dsn, connect_timeout=10)
'''

NEW = '''def _connect() -> Any:
    """Open a connection using the same settings the embedder already reads.

    Two drivers, one interface. The embedder image ships psycopg 3; the Airflow image
    ships psycopg2, because that is what its own metadata database needs. Both accept
    a DSN, %(name)s parameters and a list of dicts for executemany, so the fallback is
    a rename rather than a second code path.

    This exists because the first real DAG run failed here with ModuleNotFoundError
    after silver had already grown to 8,274 papers -- the load task was the first code
    in the project to run in the Airflow image *and* talk to the corpus database.
    """
    try:
        import psycopg
    except ModuleNotFoundError:  # the Airflow image: psycopg2 only
        import psycopg2 as psycopg

    from docpulse.embed.config import EmbeddingSettings

    return psycopg.connect(EmbeddingSettings.from_env().dsn, connect_timeout=10)
'''


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Allow psycopg or psycopg2")
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
    print("  + _connect falls back to psycopg2 when psycopg is absent")

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
