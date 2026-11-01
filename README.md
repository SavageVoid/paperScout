# DocPulse

An incremental, self-monitoring research lakehouse over arXiv (cs.LG / cs.CL / cs.AI):
bronze -> silver -> gold, embedding only the records whose content changed, and proving its own
correctness. See `CHECKPOINT.md` for the current state and how to resume.

**Status: Day 1 (bronze) verified end to end. Day 2 (silver) built and unit-tested; awaiting its first run on real data.** Embeddings, dbt and the app are not started.

## Quickstart

```bash
make init            # creates .env with generated secrets; then set ARXIV_CONTACT_EMAIL in it
make test            # unit tests, no Docker needed (pip install -r requirements-dev.txt first)
make smoke           # real arXiv ingest of one day into ./data/lake, no Docker (needs network)
make up              # Postgres+pgvector, object store, Airflow -> http://localhost:8080 (admin/admin)
make trigger         # run the DAG now;  make backfill DATE=2026-10-28 re-runs one day
make silver-dry      # parse+validate ALL bronze, print quarantine stats, write nothing
make silver          # build silver (also runs as part of the DAG);  make silver-verify re-checks it
```

## What exists

```
dags/arxiv_bronze_ingest.py    thin Airflow wiring: ensure_bucket -> ingest_to_bronze -> verify_bronze -> build_silver -> verify_silver
contracts/silver_papers.yml    the data contract: columns, types, rules, reason codes, policies (single source of truth)
src/docpulse/config.py         validated settings (refuses a request rate below arXiv's 3 s rule)
src/docpulse/storage.py        ObjectStore: S3Store (any S3 endpoint) + LocalStore (tests / no Docker)
src/docpulse/timeutil.py       as_utc(): plain-stdlib UTC (Airflow passes pendulum objects)
src/docpulse/ingest/           arxiv_client, bronze, schedule (arXiv announcement calendar), tasks, cli
src/docpulse/silver/           contract (load+validate), parse (Atom entry -> row), codecs (Parquet/JSON), build (scan, dedupe, merge, quarantine, verify), tasks, cli
tests/                         122 tests (4 need pyarrow); fakes for arXiv, S3, clock, an Airflow-style datetime, bronze partitions
docker-compose.yml, Makefile, .env.example
```

Bronze layout (one partition per logical date, raw API responses byte-for-byte):

```
bronze/arxiv/ingest_date=2026-10-28/page-00000.xml ...
bronze/arxiv/ingest_date=2026-10-28/_manifest.json     written last; present = partition complete
```

## Silver layer (Day 2)

```
silver/papers/published_month=2026-10/papers.parquet       one row per paper = its latest version
silver/quarantine/ingest_date=2026-10-29/quarantine.parquet  rejected entries, reason codes, raw XML
silver/_manifest.json                                      files, row counts, hashes, contract hash
```

- **Data contract** (`contracts/silver_papers.yml`) drives the validator, the Parquet schema and the reason codes.
  Reason codes: `bad_id`, `missing_title`, `missing_abstract`, `missing_authors`, `missing_category`, `bad_date`,
  `bad_type`, `schema_drift` (an Atom element the contract does not know).
- **Quarantine, never delete**: a bad entry is written with its reasons and raw XML. Derived columns do not
  double-report (a missing title yields `missing_title`, not also a hash error).
- **Circuit breaker**: if more than 5% of entries are quarantined, the build fails loudly and writes nothing.
  Mass `schema_drift` (arXiv adds an element) is caught this way.
- **Dedupe**: one row per paper; highest `version` wins, then `updated`, then the newest bronze snapshot. Overlapping
  bronze partitions collapse here. Duplicates are deduped, not quarantined (overlap is expected by design); this
  deliberately differs from the blueprint's "duplicate id" reason code.
- **Merged, not replaced**: bronze keeps only recent days, so month files also hold older papers. A stored row is replaced
  only by a newer (version, updated); an old bronze partition replayed later cannot overwrite a newer version.
- **Idempotent and incremental == one-shot**: every run re-reads the selected bronze partitions; files are rewritten
  only if their bytes change. `text_hash` = sha256(title + abstract) lets the embedding step ignore metadata-only changes.
- **Partitioned by month of `published`**. Consequence: revisions of OLD papers land in old month files, so expect a long
  tail of small files (a few rows each). Fine at this scale; compaction is future work.
- **Validation is plain Python driven by the YAML**, not pandera (no pandas dependency in the Airflow image); pandera or
  dbt tests can be layered on the gold tables.

## Design decisions (bronze)

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
| Silver logic: parsing, contract rules, dedupe, merge, quarantine, breaker, verify | 122 unit tests incl. mutation checks (JSON stand-in codec; bronze produced by the real Day 1 code) |
| Idempotent rerun on real data | two consecutive backfills of 2026-10-28: 17 objects each, `fetched_at` advanced, entries fingerprint `5dc450c86277a6f0` identical |
| **Not yet verified** | Parquet codec (needs pyarrow: 4 tests), the silver build on REAL arXiv data (`make silver-dry` first), the new DAG tasks in Airflow |
| **Not yet done** | ruff, CI, retention, embeddings/gold/app |
