# DocPulse

An incremental, self-monitoring research lakehouse over arXiv (cs.LG / cs.CL / cs.AI):
bronze -> silver -> gold, embedding only the records whose content changed, and proving its own
correctness. See `CHECKPOINT.md` for the current state and how to resume.

**Status: Day 1 of 5 (bronze ingest) complete and run end to end.** Silver, embeddings, dbt and the app are not started.

## Quickstart

```bash
make init            # creates .env with generated secrets; then set ARXIV_CONTACT_EMAIL in it
make test            # unit tests, no Docker needed
make smoke           # real arXiv ingest of one day into ./data/lake, no Docker (needs network)
make up              # Postgres+pgvector, object store, Airflow -> http://localhost:8080 (admin/admin)
make trigger         # run the DAG now;  make backfill DATE=2026-10-28 re-runs one day
```

## What exists

```
dags/arxiv_bronze_ingest.py    thin Airflow wiring: ensure_bucket -> ingest_to_bronze -> verify_bronze
src/docpulse/config.py         validated settings (refuses a request rate below arXiv's 3 s rule)
src/docpulse/storage.py        ObjectStore: S3Store (any S3 endpoint) + LocalStore (tests / no Docker)
src/docpulse/timeutil.py       as_utc(): plain-stdlib UTC (Airflow passes pendulum objects)
src/docpulse/ingest/           arxiv_client, bronze, schedule (arXiv announcement calendar), tasks, cli
tests/                         73 tests; fakes for arXiv, S3, clock, and an Airflow-style datetime
docker-compose.yml, Makefile, .env.example
```

Bronze layout (one partition per logical date, raw API responses byte-for-byte):

```
bronze/arxiv/ingest_date=2026-10-28/page-00000.xml ...
bronze/arxiv/ingest_date=2026-10-28/_manifest.json     written last; present = partition complete
```

## Design decisions

1. **Revisions are found by sorting on `lastUpdatedDate`.** The API's date filter only covers the
   original submission date, so v2/v3 would be invisible to it.
2. **`ingest_date` is the logical date, not wall-clock time.** Reruns hit the same partition and
   overwrite it (build manifest -> guards -> delete prefix -> write pages -> write manifest last).
3. **Fetch fully, then publish.** A failed fetch, an empty result or a truncated result never
   touches the previous good partition. Hitting `ARXIV_MAX_PAGES` is an error, not a silent cut.
4. **144-hour lookback.** arXiv makes papers public in batches (cutoff 14:00 ET, announced 20:00 ET
   Sun-Thu). A paper's `updated` is its submission time, so it can become visible ~78 h later over
   a weekend and ~126 h around long holidays. Each run re-fetches the overlap; silver dedupes it.
   Silver therefore reads ALL bronze partitions and dedupes by (arXiv id, version).
5. **Empty window = failure.** With a multi-day lookback, zero entries means the feed stalled.
6. **Freshness is measured, not assumed.** The manifest records the newest paper seen, what the
   announcement schedule says should be visible, and `behind_schedule`. Announced skips live in
   `KNOWN_SKIPPED_ANNOUNCEMENTS` (extend with `ARXIV_SKIPPED_ANNOUNCEMENTS=YYYY-MM-DD,...`); anything
   not listed is unmodelled, so it is a warning signal, not proof of an outage.
7. **Polite by construction.** One session, one request >= 3.1 s apart (below 3.0 is rejected),
   a 1-slot Airflow pool and `max_active_runs=1`, backoff on 429/5xx honouring `Retry-After`,
   a contact email in the User-Agent.
8. **Fail fast on deterministic errors** (bad config/window, empty window, rejected query);
   only transient network/arXiv errors retry.
9. **`verify_bronze`** re-hashes every page against the manifest and fails on stray or missing objects.

## Observed so far (single runs, not benchmarks)

| Observation | Value |
| --- | --- |
| Real run, 144 h window (Oct 22 - Oct 29) | 16 pages, 3,200 entries, 3,141 in window, 3,141 unique, ~8.0 MB raw (~2.5 KB/entry) |
| Run time | ~1 minute (16 requests at >= 3.1 s spacing) |
| Entries per announcement cycle | ~730-750 (two samples) |
| Source freshness on 2026-10-29 | newest entry 2026-10-27T17:59:59Z (Tuesday's cutoff). arXiv had announced no announcement on Wed Oct 28 (database migration); the deferred one was due Thu Oct 29 20:00 EDT. Correct behaviour, not a stall |

## Known limitations

- arXiv pages at most ~30,000 results deep per query; deeper windows raise `WindowTooDeepError`.
  At ~750/cycle that is roughly 6-8 weeks of reach by update time (an estimate).
- While paging, a paper updated mid-run can appear on two adjacent pages. Duplicates are possible,
  losses are not; silver dedupes.
- Overlap costs ~8 MB per daily partition (~3 GB/year); a retention or compaction rule is future work.
- The local object store (SeaweedFS) runs without authentication: local development only.
- No CI yet (Day 5). `ruff` has not been run.

## Operational notes (learned the hard way)

- **Object store:** MinIO's community edition stopped shipping Docker images (Oct 2025) and its repo
  was archived, so local dev uses SeaweedFS. On a disk with less free space than its default 30 GB
  volume size it rejects every write with `InternalError`; the compose file sets
  `-master.volumeSizeLimitMB=256 -volume.max=30`. Pin the image by digest in `.env`.
- **SELinux hosts (e.g. openSUSE):** bind mounts carry `:ro,z` so containers can read them; harmless elsewhere.
- **Airflow passes pendulum datetimes**; always convert with `timeutil.as_utc` at the boundary.
- **`docker compose exec` needs `-T`** when feeding a heredoc/stdin.

## Verification status

| Verified | How |
| --- | --- |
| Pipeline logic, windowing, idempotent reruns, failure safety, rate spacing, retries, freshness calendar | 73 unit tests, plus mutation checks on the key invariants |
| Full stack, DAG in Airflow, real arXiv, S3Store against SeaweedFS | run end to end on the developer's machine (openSUSE Tumbleweed, Docker) |
| Idempotent rerun on real data | two consecutive backfills of 2026-10-28: 17 objects each, `fetched_at` advanced, entries fingerprint `5dc450c86277a6f0` identical |
| **Not yet done** | ruff, CI, retention, anything in silver/embedding/gold/app |
