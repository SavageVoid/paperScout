"""CLI: python -m docpulse.embed.cli {build,verify,status,db-init}

Prints one JSON object per invocation so a shell can diff or grep it, and so the
output is paste-able into a chat/issue without reformatting (the Day 1/2
transcript shows how much time gets lost to wrapped terminal dumps).
"""

from __future__ import annotations

import argparse
import json
import sys

from . import compat
from .config import EmbeddingSettings
from .encoder import build_encoder


def _load_silver(store):
    return compat.load_silver(store)


def _print(payload: dict) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def cmd_build(args: argparse.Namespace) -> int:
    from .build import run as build_run
    from .db import open_db

    settings = EmbeddingSettings.from_env()
    store = compat.get_store()
    encoder = build_encoder(settings)
    with open_db(settings) as db:
        if not args.dry_run:
            db.ensure_schema(settings.dim)
        summary = build_run(
            store=store,
            db=db,
            encoder=encoder,
            settings=settings,
            silver_loader=_load_silver,
            dry_run=args.dry_run,
            limit=args.limit,
            rebuild=args.rebuild,
        )
    _print(summary)
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    from .db import open_db
    from .verify import run as verify_run

    settings = EmbeddingSettings.from_env()
    store = compat.get_store()
    encoder = build_encoder(settings)
    with open_db(settings) as db:
        result = verify_run(
            store=store,
            db=db,
            encoder=encoder,
            settings=settings,
            silver_loader=_load_silver,
            expect_complete=not args.allow_incomplete,
            hash_drift_is_error=not args.ignore_hash_drift,
        )
    _print(result)
    return 0 if result["ok"] else 1


def cmd_status(args: argparse.Namespace) -> int:
    from .db import open_db

    settings = EmbeddingSettings.from_env()
    encoder = build_encoder(settings)
    with open_db(settings) as db:
        db.ensure_schema(settings.dim)
        _print(
            {
                "settings": settings.public(),
                "model": encoder.info(),
                "stats": db.stats(encoder.fingerprint),
                "recent_runs": db.recent_runs(args.limit_runs),
                "models": db.model_fingerprints(),
            }
        )
    return 0


def cmd_db_init(args: argparse.Namespace) -> int:
    from .db import open_db

    settings = EmbeddingSettings.from_env()
    with open_db(settings) as db:
        _print({"schema": db.ensure_schema(settings.dim), "settings": settings.public()})
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="docpulse.embed")
    sub = parser.add_subparsers(dest="command", required=True)

    p_build = sub.add_parser("build", help="embed new/changed papers")
    p_build.add_argument("--dry-run", action="store_true")
    p_build.add_argument("--limit", type=int, default=None)
    p_build.add_argument("--rebuild", action="store_true", help="drop this model's vectors first")
    p_build.set_defaults(func=cmd_build)

    p_verify = sub.add_parser("verify", help="verify the table against silver")
    p_verify.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="treat missing vectors as a warning (use while incrementally filling)",
    )
    p_verify.add_argument(
        "--ignore-hash-drift",
        action="store_true",
        help="downgrade V9 (silver's text_hash vs our embedding text) to a warning",
    )
    p_verify.set_defaults(func=cmd_verify)

    p_status = sub.add_parser("status", help="counts, ledger, model info")
    p_status.add_argument("--limit-runs", type=int, default=10)
    p_status.set_defaults(func=cmd_status)

    p_init = sub.add_parser("db-init", help="create extension, tables, indexes")
    p_init.set_defaults(func=cmd_db_init)

    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except Exception as exc:
        _print(
            {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "hint": "run `make embed-preflight` first; it checks every assumption this "
                "package makes about your Day 1/2 code",
            }
        )
        return 2


if __name__ == "__main__":
    sys.exit(main())
