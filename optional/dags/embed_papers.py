"""OPTIONAL: run embeddings inside Airflow (Option B).

Why this file lives in `optional/dags/` and not `dags/`:
Airflow parses *every* .py file in dags/ and one bad file can poison the whole
DagBag (you saw how much time a single failing task cost). So this is opt-in:
copy it into dags/ only if you accept one of the two trade-offs below.

Before you copy it, pick a strategy:

(A) torch inside the Airflow image.  Add to docker/airflow/Dockerfile:
        RUN pip install --index-url https://download.pytorch.org/whl/cpu torch==2.15.0
        RUN pip install sentence-transformers==6.2.0 psycopg[binary] pgvector
    Cost: the airflow image grows from ~1.2 GB to ~3.5 GB and every rebuild
    rewrites that layer. With 8.2 GB free on /var, this is the tight option.

(B) let Airflow shell out to the embedder container.  Needs the docker CLI in the
    scheduler image plus /var/run/docker.sock mounted into it - i.e. root-equivalent
    access to the host for anything running in that container. Cheap in GB, worse
    in blast radius.

The recommended default is neither: `make embed` from a timer (cron/systemd) and
leave Airflow orchestrating bronze and silver only. If you still want it in the
DAG, set DOCPULSE_EMBED_IN_AIRFLOW=1 in the scheduler environment *after* doing
(A) or (B); until then this file defines no DAG at all, which keeps your DagBag
clean.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

ENABLED = os.environ.get("DOCPULSE_EMBED_IN_AIRFLOW", "").strip() in {"1", "true", "yes"}

if ENABLED:  # pragma: no cover - only meaningful inside Airflow
    from airflow.decorators import dag, task

    @dag(
        dag_id="docpulse_embeddings",
        description="Incremental embeddings: silver text_hash -> pgvector",
        start_date=datetime(2026, 10, 26),
        schedule="0 5 * * *",          # after the bronze/silver run at 01:00 UTC
        max_active_runs=1,             # same reason as the ingest DAG
        catchup=False,
        default_args={
            "retries": 1,
            "retry_delay": timedelta(minutes=5),
            "depends_on_past": False,
        },
        tags=["docpulse", "day3"],
    )
    def docpulse_embeddings():
        @task
        def build_embeddings() -> dict:
            from docpulse.embed.tasks import run_build_embeddings

            return run_build_embeddings()

        @task
        def verify_embeddings() -> dict:
            from docpulse.embed.tasks import run_verify_embeddings

            return run_verify_embeddings()

        verify_embeddings(build_embeddings())  # type: ignore[operator]

    docpulse_embeddings()
else:
    # Define nothing: Airflow imports this file and finds no DAGs, which is fine.
    pass
