# DocPulse — evidence ledger

Every number below was **seen on the running system**, with the command that reproduces
it. Anything not on this page is not a claim we make. Last updated 7 November 2026.

Labels: **Verified** = output seen on the box. **Reported** = from a checkpoint, not
re-observed in this pass. **Unverified** = no measurement exists; do not quote it.

---

## 1. Ingest — bronze

| claim | value | how | status |
|---|---|---|---|
| Harvest source | arXiv **Query API**, Atom export (`https://export.arxiv.org/api/query`) | `src/docpulse/config.py:32` | Verified |
| Bronze layout | `bronze/arxiv/ingest_date=YYYY-MM-DD/page-NNNNN.xml` + manifest per logical date | object store listing | Reported |
| Real run | 16 pages, 3,200 entries, 3,141 in window, 7,982,663 bytes, ~1 min | Day 1 run log | Reported |
| Later run | 19 pages, 3,800 entries, 3,603 in window | Day 2 run log | Reported |
| Rerun idempotency | 17 objects, identical **entries** fingerprint | Day 2 | Reported |
| Lookback | 144 h, because visibility lags submission by up to ~78 h | design note | Reported |
| Politeness | 3.1 s spacing (config < 3.0 s rejected), Retry-After honoured, contact in UA | config + tests | Reported |

Raw page bytes are **not** a valid idempotency measure — responses can embed request
time. The fingerprint is over entries only.

## 2. Silver

| claim | value | how | status |
|---|---|---|---|
| Papers | **8,937** (4,271 → 8,274 → 8,937 across three scheduled passes) | `select count(*) from paper_meta` | **Verified** |
| Entries processed | 13,000, overlapping | Day 2 run | Reported |
| Overlap duplicates | 8,729 (67%) → 3.04× per paper | Day 2 run | Reported |
| Monthly Parquet files | 50 | Day 2 run | Reported |
| Quarantined | **0** of 13,000 | Day 2 run | Reported — mechanism proven only by tests |
| Reason codes | 8 (`bad_id`, `missing_title`, `missing_abstract`, `missing_authors`, `missing_category`, `bad_date`, `bad_type`, `schema_drift`) | YAML contract | Reported |
| Circuit breaker | fails the build and writes nothing, at 5% | tests | Reported |
| Five-task DAG | ~70 s (ingest 44, silver 13, verify 4) | Day 2 run | Reported |
| Newest-version rule | changed **6 papers gained authors, 1 lost** | Day 6 dry run | **Verified** |

## 3. Embedding — the incremental claim

| claim | value | source | status |
|---|---|---|---|
| Full build | 4,271 papers, **6,512,326 chars**, **245.3 s** | `embedding_runs` run `embed-914d302402f1` | **Verified** |
| Unchanged rerun | **4,271 skipped**, 0 chars embedded, **1.4 s** | `embedding_runs` run `embed-289b43b3c45b` | **Verified** |
| Time saved | **99.4%** (175× faster) | derived from the two rows | **Verified** |
| Plan counters | `n_new`, `n_changed`, `n_unchanged`, `n_model_mismatch`, `chars_to_embed`, `text_hash_computed`, `n_duplicate_ids_collapsed`, `n_rows_seen`, `n_missing_id`, `n_missing_text` | `embedding_runs.notes` (jsonb) | **Verified** |
| Model pinning | `model_fp = 2d51d6d165b7d00e` on both runs | `embedding_runs` | **Verified** |
| Hash definition | `text_hash(title, abstract)` — title + abstract only | `src/docpulse/silver/parse.py:59` | **Verified** |
| Hash enforcement | silver raises `SilverVerificationError` if a row's hash doesn't match its text | `src/docpulse/silver/build.py:371` | **Verified** |
| Per-row change detection | `n_changed == 1` asserted | `tests/test_embed_plan.py:87` | **Verified** |

Consequence worth stating plainly: a **title** edit re-embeds that paper (its text
changed); a category, date or version change **skips** it (the embedded text is
identical). That is the intended trade-off, not an oversight.

## 4. Search

| claim | value | source | status |
|---|---|---|---|
| Keyword probe | strict answered **10 of 12**, sizes `[1,1,2,10,10,10,10,10,10,10]`, ≤8 rows in 3/10 | `make -f day5.mk keyword-probe` | **Verified** |
| Keyword latency | **1.02 ms mean, 2.28 ms max** (warm container) | same probe | **Verified** |
| Index use | Bitmap Index Scan on `paper_meta_fts_idx`, seqscan forced on and off | `EXPLAIN` | **Verified** |
| Author corpus | 23,445 name entries, 19,854 distinct, mean 5.49/paper, median 4, max **559** | `day6_authors_probe.py` | **Verified** |
| Name shapes | 542 non-ASCII, **8 brace-mangled folded**, 245 particles, 28 single-token, 5 case-only pairs | same probe | **Verified** |
| Author indexes | 3 GIN (`authors`, `authors_ci`, `authors_tsv`), all chosen by the planner | `EXPLAIN` | **Verified** |
| `Yann LeCun` | 1 paper (`2610.39302`), exact | `curl /search?author=…` | **Verified** |
| `Wei Wang` | 10 papers, `total_matches: 10` (saturation) | `curl` | **Verified** |
| `Yang Liu` | 20 papers | probe | **Verified** |

## 5. API

| claim | value | status |
|---|---|---|
| No parameters | **422** | **Verified** |
| `authors` on every row | populated in vector, keyword and author modes | **Verified** (three curls) |
| `/openapi.json`, `/docs` | **200** | **Verified** |
| `mode` echo | `vector` / `keyword` / `author` reported per response | **Verified** |
| `total_matches` | `null` = fewer rows than limit; integer = limit hit | **Verified** |

## 6. Tests and hygiene

| claim | value | status |
|---|---|---|
| Test suite | **426 passed, 1 warning**, 3.74 s | **Verified** |
| Lint | `ruff check src scripts dags tests` clean | **Verified** |
| DB tests | 6 embedding + 10 keyword tests run against `docpulse_test` | **Verified** |

## 7. Operations

| claim | value | status |
|---|---|---|
| Database | **`docpulse_embed`** (not `docpulse`) | **Verified** |
| Corpus intact after restart | 4,271 papers, 4,271 embeddings | **Verified** |
| Corpus after the 6 Nov scheduled ingest | **8,274 papers, 8,274 embeddings** | **Verified** |
| Backup | `docpulse_embed_2026-11-04.dump`, 11 MB, `pg_dump -Fc` | **Verified** |
| Disk | 92% used, 4.8 GB free after `docker builder prune` | **Verified** |
| Deployment | dev-grade only: unauthenticated object store, default Airflow creds | **Verified** |

---

## 8. Automation — from announcement to searchable row (Day 7)

The question this section answers: does a paper arXiv announces tonight end up in
`paper_meta` tomorrow, without a human typing a command? Yes, and each link in that
chain was observed.

| claim | value | source | status |
|---|---|---|---|
| Schedule | `@daily` (**00:00 UTC**), `start_date` 2026-10-01, `catchup=False`, `max_active_runs=1`, two retries with exponential backoff, `is_paused False` | `dags next-execution` + DAG source | **Verified** |
| DAG shape | **6 tasks**, ends at `check_meta`; embeddings run as `make embed` in the image that owns the model | `dags/arxiv_bronze_ingest.py` | **Verified** |
| Scheduled ingest | **21 pages, 4,200 entries, 4,075 in window, 10,418,609 bytes**, `stop_reason reached_window_start`, `behind_schedule False` | DAG run log | **Verified** |
| Silver build | 17,200 entries → **4,003 new** papers, 196 revised, **65 text-changed**, **0 quarantined**, 60 months | `build_silver` | **Verified** |
| Load into Postgres | **8,274 rows written**, 60 months, 8,274 read | `load_meta` → `n_written 8274` | **Verified** |
| Gate | `papers 8274, null_authors 0, empty_authors 0, missing_ci 0, missing_tsv 0`, `authors_fp c2337d98dce34d6cbf4ae82dc4e7629d` | `run_check_meta`, then `tasks test … check_meta` | **Verified** |
| Gate in isolation | ran with no upstream XCom and still passed the four integrity counts | `tasks test` | **Verified** |
| DB driver | one `_connect` tries psycopg 3, falls back to psycopg2, because the Airflow image ships only the latter | `src/docpulse/silver/load.py` | **Verified** |
| What was already automated | `scheduled__2026-10-29`, `10-30`, `10-31` all **success** — but the chain ended at `verify_silver`, one layer short of the database. This is the gap Day 7 closed, not a claim that scheduling was new | `dags list-runs` | **Verified** |
| Incremental embedding on new data | `n_new 4,003, n_changed 65, n_unchanged 4,206, n_to_embed 4,068, n_embedded 4,068, n_failed 0`, 6,212,694 chars, **300.22 s**, `embed-83d9b303f78c` | `embedding_runs` | **Verified** |
| Two counters agree | `n_changed 65` equals silver's `n_text_changed_papers 65` — hash-based skipping, measured against a real announcement rather than a no-op rerun | `embedding_runs` vs `build_silver` | **Verified** |
| One vector per paper | `(8274, 8274)` from `paper_meta` and `paper_embeddings` | `select (…), (…)` | **Verified** |
| Fingerprint moved, correctly | `b1333bbebda572f7…` at 4,271 rows → `c2337d98dce34d6c…` at 8,274; it tracks corpus content, so growth **should** change it | `authors_fp` across loads | **Verified** |
| Vectors without a human | cron runs `scripts/embed_new.sh` every 30 minutes; it compares `paper_meta` to `paper_embeddings` and exits in milliseconds when they are equal | `logs/embed_new.log` | **Verified** |
| Unattended embed on new papers | `n_new 663, n_changed 27, n_to_embed 690, n_embedded 690, n_skipped 8,247`, 1,030,288 chars, **52.653 s**, run `embed-442c2aaf6a5a`, `n_failed 0` | `embedding_runs` | **Verified** |
| Timer is idempotent | second invocation printed `papers=8937 vectors=8937` then `nothing new: every paper already has a vector` | `scripts/embed_new.sh` | **Verified** |
| Loop closed | `papers=8937 vectors=8937` — the scheduled run added 663 papers, the timer vectorised them two minutes later, with no human action | count query | **Verified** |
| Model swap | 8,937 rows re-embedded with `BAAI/bge-small-en-v1.5` @ `5c38ec7c…`; `n_dropped_for_model_change 8937`, new `model_fp 7e978d0139627414`, 13,610,365 chars, **2,433.652 s**, `n_failed 0`, run `embed-06b3da897fc7` | `embedding_runs` | **Verified** |
| Model-change guard worked | build logged `model change accepted; old spaces dropped: ['2d51d6d165b7d00e']`; `/health` returned **503** until the re-embed finished, so no query ever mixed two vector spaces | `warnings[]`, `/health` | **Verified** |
| Silent truncation, found | model window **256 tokens** while `EMBED_MAX_CHARS=4000` permits 4,000 chars; mean embedded text 1,493 chars, so **~33% of every abstract never entered its vector** and nothing warned | `max_seq_length` probe | **Verified** |
| Author contract | `mode=author` → **422** `mode must be one of vector, keyword`; author mode is inferred from `author=`. Full first-author name: recall@10 **1.000**, MRR 0.913, 3.3 ms. Bare surname: other people | curl probes + `EVAL.md` | **Verified** |
| Query instruction | bge instruction applied to queries only, live as `query_prefix` in `/health`; results unchanged within noise, keyword rows byte-identical (the control) | `EVAL_PARAPHRASE_INSTR.md` | **Verified** |
| Semantic retrieval, measured | easy tier vector **0.900** recall@10 vs keyword 0.600 (MRR 0.800 vs 0.420); med 0.400 vs 0.500; hard 0.200 vs 0.300 — n=10 per tier, ±0.18 | `EVAL.md` | **Verified** |
| Retrieval probes | one miss sits at **rank 42** of 8,937 (83 under MiniLM); another is **absent from the top 500 under both models** — rankable and unretrievable failures need different fixes | curl at limit=500 | **Verified** |

Where embeddings run, and why not in the DAG: `build_encoder()` refuses to run
without `sentence-transformers` rather than write meaningless vectors, and the Airflow
image has no torch, so the step stays out of the DAG. It is automated anyway. A host
cron entry runs `scripts/embed_new.sh` every 30 minutes, which compares `paper_meta`
to `paper_embeddings`, exits in milliseconds when they match, and otherwise embeds in the
image that owns the model. The DAG guarantees every `paper_meta` row is present and
gated; the timer guarantees each one receives a vector.

---

## Claims we may **not** make yet

| tempting claim | why not |
|---|---|
| "Quarantined N bad records" | N is **zero**. The mechanism exists; it has never fired on real data |
| "HNSW index" | Presence never checked. `EXPLAIN` on the vector path has not been run |
| "Vector search latency" | Never measured — 1.02 ms is the **keyword** path |
| "Hybrid / RRF search" | Deliberately not built; evidence-gated out at this corpus size |
| "dbt gold layer" | Not built |
| "Chaos suite" | Not built — fault injection exists only inside unit tests |
| "CI pipeline" | Not built |
| "RAG / generation" | Not built. Retrieval is the substrate; nothing generates text |
| "Pipeline success rate" | No measured series; early runs failed for bugs now fixed |
| "The DAG produces embeddings" | It deliberately stops at `check_meta`; vectors come from `make embed` in the embedder image |
| "Streamlit dashboard" | Serving layer is FastAPI + `/docs` |

## How to re-verify everything in five minutes

```bash
PYTHONPATH=src python3 -m pytest -q 2>&1 | tail -2
make -f day5.mk keyword-probe
curl -sS 'localhost:8090/search?author=Yann+LeCun' | jq '{mode, n: .n_returned, author}'
curl -sS -o /dev/null -w 'openapi=%{http_code} docs=%{http_code}\n' localhost:8090/openapi.json localhost:8090/docs
dc exec -T postgres psql -U docpulse -d docpulse_embed -c "select n_embedded, n_skipped, seconds from embedding_runs order by started_at desc limit 2"
dc run --rm --entrypoint python airflow-scheduler -c "from docpulse.silver.load import _connect; c = _connect(); cur = c.cursor(); cur.execute('select (select count(*) from paper_meta) as papers, (select count(*) from paper_embeddings) as embeddings'); print(cur.fetchone())"
```
