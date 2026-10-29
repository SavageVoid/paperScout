"""Run a bronze ingest without Airflow (smoke tests, debugging, manual backfill of one day).

    PYTHONPATH=src python -m docpulse.ingest.cli --store local --max-pages 1
    PYTHONPATH=src python -m docpulse.ingest.cli --store s3 --ingest-date 2026-10-26

It uses exactly the same code path as the DAG, so what works here works there.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import sys
from datetime import date, datetime, time, timedelta, timezone

from docpulse.config import ArxivSettings, StorageSettings
from docpulse.ingest.arxiv_client import ArxivClient
from docpulse.ingest.bronze import IngestWindow, ingest_window, verify_partition
from docpulse.storage import LocalStore, ObjectStore, S3Store


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ingest-date", type=date.fromisoformat, default=yesterday,
                   help="day of data to ingest, UTC (default: yesterday)")
    p.add_argument("--store", choices=("local", "s3"), default="local")
    p.add_argument("--local-root", default="./data/lake")
    p.add_argument("--max-pages", type=int, help="override ARXIV_MAX_PAGES (note: exceeding it is an error)")
    p.add_argument("--lookback-hours", type=int, help="override ARXIV_LOOKBACK_HOURS")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = ArxivSettings.from_env()
    overrides = {k: v for k, v in (("max_pages", args.max_pages), ("lookback_hours", args.lookback_hours)) if v is not None}
    if overrides:
        settings = dataclasses.replace(settings, **overrides)
    if not settings.has_contact_email:
        logging.warning("ARXIV_CONTACT_EMAIL is not set; please set it so arXiv can reach you")

    store: ObjectStore
    if args.store == "s3":
        s3 = S3Store.from_settings(StorageSettings.from_env())
        s3.ensure_bucket()
        store = s3
    else:
        store = LocalStore(args.local_root)

    start = datetime.combine(args.ingest_date, time.min, tzinfo=timezone.utc)
    window = IngestWindow.for_interval(start, start + timedelta(days=1), settings.lookback_hours)
    manifest = ingest_window(ArxivClient(settings), store, settings, args.ingest_date, window)
    print(json.dumps(verify_partition(store, args.ingest_date), indent=2))
    return 0 if manifest["complete"] else 1


if __name__ == "__main__":
    sys.exit(main())
