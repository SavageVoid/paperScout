# Day 3 sign-off — embeddings

Status: **closed and verified on the real stack.** 2026-11-02.

## What is verified, and how

| Claim | Evidence |
|---|---|
| 92 tests pass | `86 passed, 6 skipped` (unit) + `6 passed` (Postgres, in the embedder) |
| 4,271 papers embedded | `embed` run: `n_embedded: 4271`, 6,512,326 chars |
| Nothing skipped or orphaned | `n_missing: 0`, `n_orphans: 0`, `n_stale_text_hash: 0`, `n_rows == n_silver_papers` |
| Vectors are unit-length | `min_norm 0.99999989`, `max_norm 1.00000015` (tolerance 1e-3) |
| Vectors are distinct | `n_distinct_hashes: 4271` |
| One model wrote everything | `n_model_fps: 1`, `fingerprint 2d51d6d165b7d00e` |
| Reproducible across runs | `deterministic: true` at the pinned revision `1110a243…` |
| Airflow's metadata DB untouched | separate `docpulse_embed`; `embedding_*` tables absent from `docpulse` |
| Incremental path works | rerun `n_to_embed: 0`, `n_unchanged: 4271` |
| Resumable / single-writer | advisory-lock + crash-resume tests pass |
| Model swap is refused | `ModelChangeError` test passes |

Size: 4,271 × 384 × 4 B ≈ 6.6 MB. First run 1–3 min CPU; reruns < 1 s.

## Design decisions that are now load-bearing

- **Separate database `docpulse_embed`.** `POSTGRES_DB` is unset on this stack, so the postgres image named the database after `POSTGRES_USER` — `docpulse` *is* Airflow's metadata database. `day3_db_init.py` refuses to target it without `--allow-shared-db`.
- **Incremental on `text_hash`, keyed by `arxiv_id`.** Per-batch commit plus `pg_try_advisory_lock` gives resume-after-crash for free.
- **Model pinned by commit sha**, not tag; a floating `main` would silently change stored vectors.
- **torch lives only in `docpulse-embedder`**, never in `airflow`. Airflow cannot import it.
- **No HNSW index** — sequential scan is faster below ~100k rows; the index is a Day 5 decision with a real query workload behind it.

## Environment facts discovered the hard way

- S3 vars are `S3_ENDPOINT_URL` / `S3_REGION` / `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` — not `S3_ENDPOINT`. The overlay passes them through with `${A:-${B:-}}` so either naming in `.env` works.
- `POSTGRES_PORT=5433` is a **host** mapping; inside the container it is always 5432.
- Embedder image is ~3.5 GB on disk including build cache — heavier than estimated. Disk went 8.1 → 4.6 GB during the build.

## Open items, in priority order

1. **B2 — silver is partitioned by `published`, not `updated`.** A revised paper never moves partitions, so nothing can answer "what changed since yesterday". This blocks any freshness-aware search or incremental UI. **Day 4's first decision.**
2. ~~B4 — `text_hash` composition unverified.~~ **Resolved 2026-11-02:** the audit proved `text_hash == sha256(compose_text(title, abstract))` on all 4,271 rows. Incremental is sound; no stale-vector risk.
3. **`contract_version: null` in `silver/_manifest.json`.** Verify cannot assert that the contract YAML it reads matches the one that built the data. One line in `silver/build.py`.
4. **B1 — silver values are not range-checked** (e.g. absurd ages, empty categories) beyond element membership.
5. ~~No linter~~ — `ruff.toml` + `make -f day3.mk embed-lint` added. It found 12 issues in Day 3 code on first run (unused imports, `zip()` without `strict=`, unused loop variables); all fixed. No CI hook yet.
6. Airflow base image is unpinned by tag; `POSTGRES_PORT` confusion is host-only and undocumented in `.env`.
7. No retention policy for embedding runs or bronze objects.
8. `make embed` has no caller yet — manual command only. Deliberately deferred until a consumer exists.

## Reproduce from scratch

```bash
make -f day3.mk embed-build
make -f day3.mk embed-preflight-embedder
make -f day3.mk embed-db-init
make -f day3.mk embed-dry      # n_to_embed: 4271
make -f day3.mk embed
make -f day3.mk embed-verify   # ok: true, deterministic: true
make -f day3.mk embed-test-db  # 6 passed
```
