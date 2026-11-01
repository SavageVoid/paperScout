# Checkpoint: end of Day 1 (bronze ingest)

Date: 2026-10-30. Tag with: `git tag -a checkpoint-day1 -m "Day 1 complete"`

## Done and verified
- Idempotent rerun on real data: two consecutive backfills of 2026-10-28 -> 17 objects, `fetched_at` advanced,
  entries fingerprint `5dc450c86277a6f0` identical (raw page digest also identical, so the digest cannot
  distinguish; likely CDN caching of identical URLs: hypothesis).
- Compose stack runs: Postgres 16 + pgvector, SeaweedFS (S3 API), Airflow 2.10.5 LocalExecutor.
- DAG `arxiv_bronze_ingest` green end to end on real arXiv data (ensure_bucket -> ingest -> verify).
- 73 unit tests pass; key invariants mutation-checked (partition clear, rate limiter, stop rule,
  empty-window guard, lookback default, Airflow-style datetimes).
- Real measurement: 16 pages / 3,200 entries / 3,141 unique in window / ~8.0 MB for a 144 h window.

## Bugs found by running it for real (all fixed, each has a regression test)
1. Pendulum datetimes lost timezone info in window arithmetic -> `timeutil.as_utc`.
2. Empty/inverted Airflow interval silently became a valid lookback window -> validate first.
3. SeaweedFS rejected writes on a small disk -> smaller volumes (`-master.volumeSizeLimitMB=256 -volume.max=30`).
4. 6 h lookback assumed arXiv publishes continuously; it publishes in daily batches -> 144 h lookback,
   empty-window failure, schedule-based freshness check.

## Incidents handled
- 2026-10-29: DNS inside the containers went stale after a network change; all 5 retries failed before any
  write, the partition stayed intact, and the next run completed after restarting the stack.
- A pasted multi-line block was swallowed by `docker compose exec` without `-T` (Makefile now uses `-T`).

- 2026-10-28/29: arXiv database migration, no Wed announcement (announced on their blog). The feed's newest
  entry stayed at Tuesday's cutoff; my freshness check false-alarmed until the skip list was added.

## Open items / risks
- Watch that the arXiv feed catches up after the deferred 2026-10-29 20:00 EDT announcement: expected
  `newest_seen` ~ 2026-10-29T17:59:59Z within a few hours (prediction, not yet observed).
- Disk: ~4.8 GB free at last check. Day 3 (embedding model + PyTorch) needs several GB: check `docker system df`.
- Bronze overlap costs ~8 MB/day; add retention before running for months.
- No ruff run, no CI.
- Local compose has host-specific bits kept on purpose: `,z` mount labels (SELinux) and a digest-pinned image in `.env`.

## Next: Day 2, silver layer
1. `contracts/silver_papers.yml`: columns, types, rules (the data contract).
2. Parse Atom pages -> rows (id, version, title, abstract, authors, categories, published, updated, links).
3. Read ALL bronze partitions; dedupe by (arXiv id, version), keep the latest version per paper.
4. Validate; route bad rows to a quarantine table with reason codes
   (missing abstract, bad date, duplicate id, schema drift).
5. Write Parquet partitioned by month; determinism test (same bronze -> byte-identical silver).
6. Add `pyarrow` (+ `tzdata` if missing) to `requirements.txt`; rebuild once with `make up`.

## How to resume
```bash
cd ~/docpulse && source .venv/bin/activate
make test && make up && make ps        # expect 73 tests OK, 4 services up
```
