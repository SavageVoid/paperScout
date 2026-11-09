# PaperScout_DocPulse

Paper search over arXiv, built as a pipeline rather than a notebook: papers are harvested
from the arXiv API, cleaned into a versioned silver layer, loaded into Postgres, embedded,
and served through three retrieval modes.

The claims this project makes are narrow, and each one has a measurement behind it:

> A paper arXiv announces tonight is searchable tomorrow, with no human in the loop.
>
> Only papers whose title or abstract actually changed are re-embedded.
>
> Exact-match and author retrieval are effectively solved. Semantic retrieval is
> **measured**, and it is only as good as the query — see [EVAL.md](EVAL.md).

**Status:** ingest, silver, load, embeddings and retrieval are built, scheduled and
measured. There is **no LLM and no RAG answer layer** — retrieval is the substrate,
generation is the next milestone.

## At a glance

| | |
|---|---|
| papers | **8,937** |
| embeddings | **8,937** — one vector per paper, `(8937, 8937)` |
| embedding model | `BAAI/bge-small-en-v1.5` @ `5c38ec7c…`, 384 dim, 512-token window |
| silver | 8,937 rows across **60 months**, 0 quarantined |
| schedule | one Airflow DAG, 7 tasks, `@daily` at 00:00 UTC, `catchup=False` |
| vectors | refreshed by cron every 30 min; no-ops in milliseconds when nothing is new |
| title search | keyword recall@10 **1.000**, MRR **1.000**, 4.5 ms |
| author search | recall@10 **1.000**, MRR **0.913**, 3.3 ms (full name required) |
| plain-language query | vector recall@10 **0.900** vs keyword 0.600 |
| incremental embed | 4,068 embedded / 4,206 skipped in 300 s; later 690 embedded in 53 s |
| tests | **426 passed**, `ruff` clean |

## Quickstart

```bash
dc up -d
```

```bash
curl -sS localhost:8090/health | jq '{ok, db, n_vectors, model_matches}'
```

Expect `ok: true`, `n_vectors: 8937`, `model_matches: true`. This endpoint probes Postgres
**and** compares the stored model fingerprint against the encoder's, returning **503 with a
reason** when either disagrees. A container can be `Up` with a dead database or a stale
vector space behind it; this is the only check that knows.

Search it:

```bash
curl -sS 'localhost:8090/search?q=graph+neural+networks&mode=vector&limit=5' | jq '.results[] | {arxiv_id, title}'
```

```bash
curl -sS 'localhost:8090/search?author=Yann+LeCun' | jq '{mode, n: .n_returned, total_matches}'
```

Interactive docs at `localhost:8090/docs`, schema at `localhost:8090/openapi.json`.

**API contract worth knowing before you write a client:** `mode` accepts only `vector` or
`keyword`. Author mode is inferred from the presence of `author=`; sending `mode=author`
returns **422** `mode must be one of vector, keyword`.

## What exists

| layer | location | what it holds |
|---|---|---|
| Bronze | object store `docpulse`, `bronze/arxiv/ingest_date=YYYY-MM-DD/page-NNNNN.xml` | raw Atom XML plus a per-day manifest |
| Silver | object store, monthly Parquet | one row per paper, newest version wins, plus `text_hash` |
| Postgres | `paper_meta`, `paper_embeddings`, `embedding_runs` | searchable metadata, vectors, and an audit ledger |
| Airflow | `dags/arxiv_bronze_ingest.py` | the 7-task chain that runs daily |
| API | `src/docpulse/search/api.py` | `/search`, `/health`, `/docs` |
| Embedder | `docker/embed/Dockerfile` | sentence-transformers, the only image with torch |
| Evaluation | `eval/`, `EVAL.md` | golden sets and measured retrieval quality |

Services on the box: Postgres 16 with pgvector, SeaweedFS (S3-compatible, `127.0.0.1:8333`),
an Airflow scheduler and webserver (`127.0.0.1:8080`), and the search API on **host 8090** →
container 8080, because host 8080 was already taken.

## The pipeline runs itself

One DAG, `arxiv_bronze_ingest`, `@daily` at 00:00 UTC, `start_date` 2026-10-01,
`catchup=False`, `max_active_runs=1`, two retries with exponential backoff.

```
ensure_bucket → ingest_to_bronze → verify_bronze → build_silver → verify_silver
              → load_meta → check_meta
```

The chain **ends at the integrity gate**. `check_meta` asserts four counts — null authors,
empty author arrays, missing case-insensitive author index, missing text-search column — and
fails the run rather than publishing a corpus that cannot answer author queries.

Embeddings are **not** in the DAG, deliberately: `build_encoder()` raises without
`sentence-transformers` rather than write meaningless vectors, and the Airflow image has no
torch. They run from a host cron entry instead, every 30 minutes:

```
paper_meta has rows without vectors?  →  embed them in the image that owns the model
equal counts?                         →  exit in milliseconds, do nothing
```

That timer also refuses to act on a model change: it asks `/health`, and if the stored
fingerprint disagrees with the encoder's it logs the mismatch and **exits 4**. A 40-minute
re-embed across 8,937 rows is a decision, not something a cron job makes at 03:00.

Measured on a real scheduled pass:

```
ingest_to_bronze   21 pages · 4,200 entries · 4,075 in window · 10,418,609 bytes
build_silver       17,200 entries → 4,003 new · 196 revised · 65 text-changed · 0 quarantined
load_meta          8,274 rows written · 60 months
check_meta         papers 8274 · null_authors 0 · empty_authors 0 · missing_ci 0 · missing_tsv 0
```

and on the following pass, with no human involved at any point:

```
00:00  scheduled__2026-11-08   success  (7 tasks, 98 s)
19:51  timer: papers=8937 vectors=8274 → 690 embedded, 0 failed, 52.6 s
19:58  timer: papers=8937 vectors=8937 → nothing new
```

## The incremental claim

`text_hash` is defined over **title and abstract only**. A title or abstract edit re-embeds
that paper; a category, date or version change skips it. That is the intended trade-off.

A no-op rerun proves a skip path exists. It does not prove the path discriminates. This does:

| measurement | code path | value |
|---|---|---|
| `n_changed` | embedding planner | **65** |
| `n_text_changed_papers` | silver build | **65** |

Two independent counters, different code, same announcement, same answer. On that run 4,206
papers were skipped because their searchable text was identical, and exactly 65 were
re-embedded because it had genuinely changed.

The model is pinned by fingerprint, so a silent upgrade appears as a ledger mismatch instead
of quietly different vectors. Changing it is a deliberate act:

```bash
dc run --rm -e EMBED_ALLOW_MODEL_CHANGE=1 searchapi python -m docpulse.embed.cli build
```

which logs `model change accepted; old spaces dropped: ['2d51d6d165b7d00e']` and re-embeds
everything. Without that flag the build refuses.

## Retrieval quality

Measured with `scripts/day8_eval.py` over golden sets built from the corpus itself, so no
expected answer was written from memory. Full detail and the misses: [EVAL.md](EVAL.md).

**Queries whose words appear in the paper** (180 queries):

| kind | mode | recall@10 | MRR | mean ms |
|---|---|---|---|---|
| exact title | keyword | 1.000 | **1.000** | 4.5 |
| 8 consecutive words | keyword | 1.000 | 0.983 | 4.2 |
| exact title | vector | 1.000 | 0.969 | 35 |
| 8 consecutive words | vector | 0.617 | 0.455 | 35 |
| full author name | author | 1.000 | 0.913 | 3.3 |

**Queries that share meaning but not words** (30 queries, same 10 papers in each tier):

| tier | keyword | vector |
|---|---|---|
| easy — title restated plainly | 0.600 recall / 0.420 MRR | **0.900 recall / 0.783 MRR** |
| med — core claim, no jargon | **0.500 / 0.450** | 0.400 / 0.400 |
| hard — oblique, no shared words | **0.300 / 0.124** | 0.200 / 0.111 |

Read plainly: **keyword owns literal queries, vector wins when a person describes what they
want, and both fail on riddles.** Vector latency is ~52 ms warm and flat; keyword ranges from
4.5 ms on a title to 120–168 ms on a nine-word question, because Postgres widens the scan.

Where the misses live, measured at `limit=500` of 8,937: one missed paper sits at **rank 42**
(retrievable, outranked — a ranking problem, next fix is a cross-encoder reranker over the
candidate pool), and another is **absent from the top 500** under both models (not
represented — a 1,500-character abstract compressed into one vector; the fix is passage
chunking, at a measured 40 minutes of re-embedding per attempt).

## Design decisions

| decision | reason |
|---|---|
| Entries fingerprint, not page bytes | raw responses can embed request time; only entries are a valid idempotency measure |
| 144-hour lookback, 3.1 s spacing | arXiv visibility lags submission by up to ~78 h; config rejects anything under 3.0 s |
| Newest version wins in silver | otherwise a revision leaves a stale author list; measured at 6 gained / 1 lost |
| Circuit breaker at 5% bad records | fail the build and write nothing, rather than publish a partial corpus |
| `text_hash(title, abstract)` | the embedded text is the searchable text; metadata churn must not cost a vector |
| Model pinned by fingerprint | an unpinned upgrade is a silent change in every result |
| Gate inside the DAG | a corpus that cannot answer author queries must fail at load, not at query time |
| bge-small over MiniLM | MiniLM's 256-token window silently discarded ~33% of every abstract; bge covers it at 512 tokens, same 384 dims |
| Query instruction on the query side only | bge is trained that way; documents must never receive it, so it is applied at the single query call site |
| Query prefix kept out of the stored fingerprint | it changes nothing in the table; including it would trigger a pointless full re-embed |
| No RRF / hybrid fusion | measured first instead: the two modes win on different query types, which is more useful than averaging them |
| Reranker and chunking not built yet | each fixes a *different* measured failure; building either before the measurement would have been guesswork |

## Verification

Every number in this README is reproduced by a command in [EVIDENCE.md](EVIDENCE.md), which
also lists the claims the project **refuses** to make. Five-minute check:

```bash
curl -sS localhost:8090/health | jq '{ok, db, n_vectors, model_matches, query_prefix}'
```

```bash
dc run --rm --entrypoint python airflow-scheduler -c "from docpulse.silver.load import _connect; c = _connect(); cur = c.cursor(); cur.execute('select (select count(*) from paper_meta) as papers, (select count(*) from paper_embeddings) as embeddings'); print(cur.fetchone())"
```

```bash
python3 scripts/day8_eval.py --golden eval/paraphrase30.json
```

## Known limitations

1. **No RAG, no generation.** Retrieval only; nothing writes an answer.
2. **Semantic retrieval is weak on abstract-level and oblique queries** — 0.400 and 0.200
   recall@10. Measured, not hidden, and the two causes are known.
3. **Precision is unmeasured.** The golden sets expect one paper per query; whether the other
   nine results were any good is not evaluated.
4. **No CI.** 426 tests pass locally; nothing runs them on push. The eval scripts are not yet
   part of `pytest`, so a stale script fails loudly only if you read the output.
5. **Quarantine has never fired on real data.** 0 of 13,000 entries were bad; the mechanism is
   proven by tests only.
6. **HNSW is opt-in** (`make -f day4.mk search-index`) and its presence has never been
   confirmed by `EXPLAIN`, so vector latency is exact-search latency.
7. **Accents are not folded.** `Herve Panetto` will not find `Hervé Panetto`.
8. **Homonyms are unresolvable** — arXiv ships no author identifiers.
9. **No authentication.** Dev-grade: unauthenticated object store, default Airflow creds,
   single node. Not a deployment.

## Operational notes (learned the hard way)

- The live database is **`docpulse_embed`**, not `docpulse`. Pointing `psql` at the wrong one
  reports `paper_meta does not exist` while the API answers happily.
- **Container status says nothing about the database.** Postgres was down for 11 hours with
  `searchapi` `Up`; two containers sat dead for 37 hours unnoticed. Policies are now
  `restart: unless-stopped` on postgres, objectstore, airflow-webserver and
  airflow-scheduler. `airflow-init` is a one-shot and stays at `no`.
- **A paused DAG does not schedule task instances either.** Pausing mid-run froze a run for
  two days; unpausing resumed it at the exact task it stopped on.
- `./src` is bind-mounted **read-only** — `docker cp` into it fails. New files need
  `chcon -t container_file_t`.
- **Two images, two DB drivers.** The embedder ships psycopg 3, the Airflow image ships
  psycopg 2. All code goes through one `_connect()` that tries both.
- **Run S3-facing code through `dc run`, not the host venv.** `SignatureDoesNotMatch` from a
  host shell is a host credential or clock problem.
- **Truncation is silent.** `EMBED_MAX_CHARS=4000` looked permissive while the model kept 256
  tokens. Any future model change should check `max_seq_length` first.
- **The embedding operating point lives in `.env`**, which is untracked. Mirror it in
  `.env.example` or the repo cannot reproduce its own vectors.
- **Never `dc down -v` and never `docker volume prune`.** The volumes are the corpus.

## Repository map

```
dags/                 the Airflow DAG
src/docpulse/         bronze, silver, embed, search packages
tests/                unit and DB-backed tests
contracts/            the YAML contract for silver
docker/               airflow and embed images
eval/                 golden question sets (JSON, corpus-derived)
scripts/              entry points, the embed timer, evaluation, one-off migrations
EVIDENCE.md           every claim, with the command behind it
EVAL.md               retrieval quality per query type, including the failures
PLAN_DAY9.md          costed plan for the two known retrieval fixes
README_DAY7.md        the Day-7 narrative: how the pipeline became unattended
```

## Tests

```bash
PYTHONPATH=src python3 -m pytest -q
```

```bash
ruff check src scripts dags tests
```

426 passing. DB-backed suites run against a scratch `docpulse_test` database so they never
touch the live corpus.
