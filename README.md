# PaperScout

An incremental, self-monitoring research lakehouse over arXiv (cs.LG / cs.CL / cs.AI):
bronze → silver → gold, embedding only the records whose content changed, and proving its own
correctness. See the project blueprint for the full plan.

**Status: Day 1 of 5 (bronze ingest) complete.** Silver, embeddings, dbt and the app are not started.

## Quickstart

```bash
make init            # creates .env with generated secrets; then set ARXIV_CONTACT_EMAIL in it
make up              # Postgres+pgvector, object store, Airflow  ->  http://localhost:8080 (admin/admin)
make test            # unit tests (no Docker needed)
make smoke           # ONE real arXiv page into ./data/lake (no Docker, needs network)
```

The DAG is unpaused on creation, so the scheduler runs the most recent complete day as soon as the
stack is up. Re-run a specific day with `make backfill DATE=2026-10-26`.

## What exists

```
dags/arxiv_bronze_ingest.py    thin Airflow wiring: ensure_bucket -> ingest_to_bronze -> verify_bronze
src/docpulse/config.py         validated settings (refuses an arXiv request rate below the 3 s rule)
src/docpulse/storage.py        ObjectStore: S3Store (any S3 endpoint) + LocalStore (tests / no Docker)
src/docpulse/ingest/           arxiv_client (rate limit, retries, parsing), bronze (windowing, publish, verify), tasks, cli
tests/                         59 tests; fakes for arXiv, S3 and the clock
docker-compose.yml, Makefile, .env.example
```

Bronze layout (one partition per logical date, raw API responses byte-for-byte):

```
bronze/arxiv/ingest_date=2026-10-26/page-00000.xml ...
bronze/arxiv/ingest_date=2026-10-26/_manifest.json     written last; present = partition complete
```

## Day 1 design decisions

1. **Revisions are found by sorting on `lastUpdatedDate`.** The API's date filter only covers the
   original submission date, so v2/v3 would be invisible to it. We page newest-first until a page
   reaches the window start.
2. **`ingest_date` is the logical date, not wall-clock time.** Reruns and backfills hit the same
   partition and overwrite it (delete prefix → write pages → write manifest last).
3. **Fetch fully, then publish.** Pages are collected in memory first, so an arXiv outage mid-run
   leaves the previous good partition untouched. Hitting `ARXIV_MAX_PAGES` is an error, never a
   silent truncation.
4. **6-hour lookback overlap.** arXiv indexes with some lag; a strict daily window could permanently
   miss papers submitted just before midnight. The overlap re-fetches a few hours; silver dedupes.
5. **Polite by construction.** One session, one request every ≥3.1 s (config below 3.0 is rejected),
   a 1-slot Airflow pool and `max_active_runs=1` so two tasks can never hit arXiv concurrently,
   backoff on 429/5xx honouring `Retry-After`, and a User-Agent carrying your contact email.
6. **`verify_bronze` task** re-hashes every page against the manifest and fails on stray or missing objects.

## Known limitations

- **arXiv pages at most ~30,000 results deep per query.** Windows older than that cannot be reached by
  `lastUpdatedDate` sorting; the code raises `WindowTooDeepError` instead of returning partial data.
  How many days that is depends on daily volume, which is not yet measured: the first run's manifest
  (`n_entries_total`) will tell you. Deep history needs a different source (OAI-PMH or a dump).
- While paging, a paper updated mid-run can shift entries so one appears on two adjacent pages.
  Duplicates are possible, losses are not. Silver dedupes by (id, version).
- Pages outside the window (the page that straddles its edges) are kept raw; **silver must filter on
  the manifest's `window`** (Day 2).
- The local object store (SeaweedFS) runs without authentication. Local development only.
- No CI yet (Day 5).

## Object store note

The blueprint specifies MinIO. MinIO's community edition stopped publishing Docker images in October
2025 and its repository was archived in April 2026, so this project uses SeaweedFS locally. Only the S3
API is used: set `OBJECTSTORE_IMAGE`/`command` for another server, or point `S3_ENDPOINT_URL` at
AWS S3 and nothing else changes.

## Verification status (honest)

| Verified | How |
| --- | --- |
| Pipeline logic, windowing, idempotent reruns, failure safety, rate spacing, retries, S3 batching | 59 unit tests (plus mutation checks on the key invariants) |
| CLI path end to end | run twice against a fake arXiv: identical output |
| Compose YAML, Makefile | parsed / dry-run only |
| **Not yet run** | `docker compose up`, DAG import inside Airflow, a real arXiv request, S3Store against a real server, ruff |
