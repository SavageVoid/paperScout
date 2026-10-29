"""Task bodies for the bronze DAG, kept free of Airflow imports so they are unit-testable.

The DAG file only wires these together; all behaviour lives (and is tested) here.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any

from docpulse.config import ArxivSettings, StorageSettings
from docpulse.ingest.arxiv_client import ArxivClient
from docpulse.ingest.bronze import IngestWindow, ingest_window, summarize_manifest, verify_partition
from docpulse.timeutil import as_utc
from docpulse.storage import ObjectStore, S3Store


log = logging.getLogger(__name__)


def ingest_date_for(interval_start: datetime) -> date:
    """Partition key = the UTC day the data interval starts on (the logical date)."""
    return as_utc(interval_start).date()


def _default_store() -> S3Store:
    return S3Store.from_settings(StorageSettings.from_env())


def run_ensure_bucket(store: S3Store | None = None) -> str:
    store = store or _default_store()
    store.ensure_bucket()
    return store.bucket


def run_bronze_ingest(
    interval_start: datetime,
    interval_end: datetime,
    *,
    settings: ArxivSettings | None = None,
    store: ObjectStore | None = None,
    client: Any | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    settings = settings or ArxivSettings.from_env()
    if not settings.has_contact_email:
        log.warning("ARXIV_CONTACT_EMAIL is a placeholder; set a real address so arXiv can reach you")
    window = IngestWindow.for_interval(interval_start, interval_end, settings.lookback_hours)
    ingest_date = ingest_date_for(interval_start)
    manifest = ingest_window(
        client or ArxivClient(settings), store or _default_store(), settings, ingest_date, window, now=now
    )
    return summarize_manifest(manifest)


def run_verify_bronze(summary: dict[str, Any], *, store: ObjectStore | None = None) -> dict[str, Any]:
    verified = verify_partition(store or _default_store(), date.fromisoformat(summary["ingest_date"]))
    if verified != summary:
        raise RuntimeError(f"verified partition differs from ingest summary: {verified} != {summary}")
    return verified
