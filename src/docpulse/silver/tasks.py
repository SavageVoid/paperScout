"""Task bodies for the silver steps of the DAG (Airflow-free, unit-testable)."""

from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path
from typing import Any

from docpulse.config import StorageSettings
from docpulse.silver.build import CodecFactory, build_silver, verify_silver
from docpulse.silver.codecs import ParquetCodec
from docpulse.silver.contract import Contract, load_contract
from docpulse.storage import ObjectStore, S3Store

CONTRACT_FILE = "silver_papers.yml"


def default_contract_path() -> Path:
    env = os.environ.get("DOCPULSE_CONTRACT")
    if env:
        return Path(env)
    for base in (Path("/opt/airflow/contracts"), Path("contracts")):
        if (base / CONTRACT_FILE).exists():
            return base / CONTRACT_FILE
    raise FileNotFoundError(f"{CONTRACT_FILE} not found; set DOCPULSE_CONTRACT")


def _store() -> ObjectStore:
    return S3Store.from_settings(StorageSettings.from_env())


def run_build_silver(
    *,
    store: ObjectStore | None = None,
    contract: Contract | None = None,
    codec_factory: CodecFactory = ParquetCodec,
    since: date | None = None,
    dry_run: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    report = build_silver(
        store or _store(),
        contract or load_contract(default_contract_path()),
        codec_factory,
        since=since,
        dry_run=dry_run,
        now=now,
    )
    return report


def summarize(report: dict[str, Any]) -> dict[str, Any]:
    """The small subset that travels through XCom."""
    keys = (
        "n_entries", "n_valid_entries", "n_quarantined", "quarantine_by_reason", "n_unique_papers_in_bronze",
        "n_new_papers", "n_revised_papers", "n_text_changed_papers", "n_papers_total",
    )
    return {k: report[k] for k in keys if k in report} | {"contract_version": report["contract"]["version"]}


def run_verify_silver(
    summary: dict[str, Any] | None = None,
    *,
    store: ObjectStore | None = None,
    contract: Contract | None = None,
    codec_factory: CodecFactory = ParquetCodec,
) -> dict[str, Any]:
    verified = verify_silver(store or _store(), contract or load_contract(default_contract_path()), codec_factory)
    if summary is not None and summary.get("n_papers_total") != verified["n_papers_total"]:
        raise RuntimeError(f"verified silver differs from build summary: {verified} vs {summary}")
    return verified
