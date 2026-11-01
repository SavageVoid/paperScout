"""Run the silver build without Airflow.

    python -m docpulse.silver.cli --dry-run          # parse + validate bronze, write nothing
    python -m docpulse.silver.cli                    # build
    python -m docpulse.silver.cli --verify-only
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date

from docpulse.config import StorageSettings
from docpulse.silver.codecs import JsonCodec, ParquetCodec
from docpulse.silver.contract import load_contract
from docpulse.silver.tasks import default_contract_path, run_build_silver, summarize
from docpulse.silver.build import verify_silver
from docpulse.storage import LocalStore, ObjectStore, S3Store


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--store", choices=("s3", "local"), default="s3")
    p.add_argument("--local-root", default="./data/lake")
    p.add_argument("--codec", choices=("parquet", "json"), default="parquet")
    p.add_argument("--since", type=date.fromisoformat, help="only bronze partitions on/after this date")
    p.add_argument("--dry-run", action="store_true", help="parse and validate, print the report, write nothing")
    p.add_argument("--verify-only", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    store: ObjectStore = S3Store.from_settings(StorageSettings.from_env()) if args.store == "s3" else LocalStore(args.local_root)
    codec_factory = ParquetCodec if args.codec == "parquet" else JsonCodec
    contract = load_contract(default_contract_path())

    if args.verify_only:
        print(json.dumps(verify_silver(store, contract, codec_factory), indent=2))
        return 0
    report = run_build_silver(store=store, contract=contract, codec_factory=codec_factory,
                              since=args.since, dry_run=args.dry_run)
    print(json.dumps(report if args.dry_run else summarize(report) | {"months_written": report["months_written"]},
                     indent=2, default=str))
    if not args.dry_run:
        print(json.dumps(verify_silver(store, contract, codec_factory), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
