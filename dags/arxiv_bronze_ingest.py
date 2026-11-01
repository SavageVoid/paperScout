"""
### arXiv -> bronze -> silver

Daily ingest of new and revised cs.LG / cs.CL / cs.AI papers from the arXiv API into
object storage, as raw Atom pages plus a manifest (bronze). One partition per logical date;
reruns overwrite that partition (idempotent). Then the silver build parses, validates against
the YAML data contract, dedupes by paper (latest version wins), quarantines bad entries with
reason codes, and merges into monthly Parquet files.

Run exactly one at a time: arXiv allows a single connection, so the ingest task uses the
`arxiv_api` pool (1 slot, created by `airflow-init`) and the DAG allows one active run.
"""

from __future__ import annotations

from datetime import timedelta

import pendulum
from airflow.decorators import dag, task
from airflow.exceptions import AirflowFailException

# Imports of docpulse.* are done inside tasks so a dependency problem shows up as a task
# failure with a clear traceback instead of a DAG import error.


@dag(
    dag_id="arxiv_bronze_ingest",
    schedule="@daily",
    start_date=pendulum.datetime(2026, 10, 1, tz="UTC"),
    catchup=False,  # backfills are explicit and bounded (see README: arXiv API depth limit)
    max_active_runs=1,
    default_args={
        "owner": "docpulse",
        "retries": 2,
        "retry_delay": timedelta(minutes=5),
        "retry_exponential_backoff": True,
        "max_retry_delay": timedelta(minutes=30),
    },
    tags=["docpulse", "bronze", "arxiv"],
    doc_md=__doc__,
)
def arxiv_bronze_ingest():
    @task
    def ensure_bucket() -> str:
        from docpulse.ingest.tasks import run_ensure_bucket

        return run_ensure_bucket()

    @task(pool="arxiv_api", execution_timeout=timedelta(minutes=45))
    def ingest_to_bronze(bucket: str, data_interval_start=None, data_interval_end=None) -> dict:
        from docpulse.ingest.arxiv_client import ArxivAPIError
        from docpulse.ingest.bronze import BronzeIngestError
        from docpulse.ingest.tasks import run_bronze_ingest

        try:
            return run_bronze_ingest(data_interval_start, data_interval_end)
        except (ValueError, BronzeIngestError, ArxivAPIError) as exc:
            # Deterministic problems (bad config/window, window too deep, rejected query):
            # retrying cannot help, so fail now instead of after 15+ minutes of retries.
            # Transient network/arXiv errors are NOT caught here and still retry.
            raise AirflowFailException(str(exc)) from exc

    @task
    def verify_bronze(summary: dict) -> dict:
        from docpulse.ingest.tasks import run_verify_bronze

        return run_verify_bronze(summary)

    @task
    def build_silver(_verified_bronze: dict) -> dict:
        from docpulse.silver.build import SilverError
        from docpulse.silver.tasks import run_build_silver, summarize

        try:
            return summarize(run_build_silver())
        except (SilverError, ValueError, FileNotFoundError) as exc:
            # Contract violations, missing bronze and a malformed contract are deterministic:
            # retrying cannot fix them, so fail now with the reason.
            raise AirflowFailException(str(exc)) from exc

    @task
    def verify_silver(summary: dict) -> dict:
        from docpulse.silver.tasks import run_verify_silver

        return run_verify_silver(summary)

    verify_silver(build_silver(verify_bronze(ingest_to_bronze(ensure_bucket()))))


arxiv_bronze_ingest()
