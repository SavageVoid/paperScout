# DocPulse

Paper search over arXiv, built as a pipeline rather than a notebook: papers are harvested
from the arXiv API, cleaned into a versioned silver layer, loaded into Postgres, embedded,
and served through three retrieval modes.

The claim this project makes is narrower than "search works". It is:

> A paper arXiv announces tonight is searchable tomorrow, and only the papers whose text
> actually changed are re-embedded.

Both halves are measured on live data, not fixtures — see [EVIDENCE.md](EVIDENCE.md).

**Status:** ingest, silver, load, embeddings and retrieval are built and running on a
schedule. There is **no LLM and no RAG answer layer yet** — retrieval is the substrate,
generation is the next milestone.

## At a glance

| | |
|---|---|
| papers | **8,274** |
| embeddings | **8,274** — one vector per paper, `(8274, 8274)` |
| silver | 8,274 rows across **60 months**, 0 quarantined |
| schedule | one Airflow DAG, `@daily` at 00:00 UTC, `catchup=False` |
| incremental embed | **4,068** embedded, **4,206** skipped, **0** failed, 300 s |
| full rebuild | 4,271 papers, 6,512,326 chars, 245.3 s |
| no-op rerun | 4,271 skipped, **1.4 s** — 99.4% saved |
| keyword latency | **1.02 ms** mean, 2.28 ms max (warm) |
| tests | **426 passed**, `ruff` clean |

## Quickstart

Bring the stack up. Data lives in named volumes and survives a restart; `down` never
takes `-v`.

```bash
dc up -d
```

Check that the API is actually talking to the database, not merely running:

```bash
curl -sS localhost:8090/health | jq
```

Expect `ok: true`, `db: "up"`, `n_vectors: 8274`. This endpoint probes Postgres and
returns **503 with a reason** when a check fails, because a container can be `Up` with a
dead connection behind it.

Search it:

```bash
curl -sS 'localhost:8090/search?q=graph+neural+networks&mode=vector&limit=5' | jq '.hits[] | {arxiv_id, title}'
```

```bash
curl -sS 'localhost:8090/search?author=Yann+LeCun' | jq '{mode, n: .n_returned, total_matches}'
```

Interactive API docs at `localhost:8090/docs`, schema at `localhost:8090/openapi.json`.

## What exists

| layer | location | what it holds |
|---|---|---|
| Bronze | object store `docpulse`, `bronze/arxiv/ingest_date=YYYY-MM-DD/page-NNNNN.xml` | raw Atom XML plus a per-day manifest |
| Silver | object store, monthly Parquet | one row per paper, newest version wins, plus `text_hash` |
| Postgres | `paper_meta`, `paper_embeddings`, `embedding_runs` | searchable metadata, vectors, and an audit ledger |
| Airflow | `dags/arxiv_bronze_ingest.py` | the six-task chain that runs daily |
| API | `src/docpulse/search/api.py` | `/search`, `/health`, `/docs` |
| Embedder | `docker/embed/Dockerfile` | sentence-transformers, only image with torch |

Services on the box: Postgres 16 with pgvector, SeaweedFS (S3-compatible, `127.0.0.1:8333`),
an Airflow scheduler and webserver (`127.0.0.1:8080`), and the search API on
**host 8090** → container 8080, because host 8080 was already taken.

## The pipeline runs itself

One DAG, `arxiv_bronze_ingest`, `@daily` at 00:00 UTC, `start_date` 2026-10-01,
`catchup=False`, `max_active_runs=1`, two retries with exponential backoff.

```
ensure_bucket → ingest_to_bronze → verify_bronze → build_silver → verify_silver
              → load_meta → check_meta
```

The chain **ends at the integrity gate**. `check_meta` asserts four counts — null authors,
empty author arrays, missing case-insensitive author index, missing text-search column —
and fails the run rather than publishing a corpus that cannot answer author queries.

Embeddings are **not** in the DAG, deliberately. `build_encoder()` raises without
`sentence-transformers` instead of writing meaningless vectors, and the Airflow image has
no torch. Vectors come from `make embed` in the image that owns the model. What the DAG
guarantees is stated just as plainly: every `paper_meta` row is present and valid. It does
not guarantee a vector.

Measured on a real scheduled pass:

```
ingest_to_bronze   21 pages · 4,200 entries · 4,075 in window · 10,418,609 bytes
                   stop_reason reached_window_start · behind_schedule False
build_silver       17,200 entries → 4,003 new · 196 revised · 65 text-changed · 0 quarantined
load_meta          8,274 rows written · 60 months
check_meta         papers 8274 · null_authors 0 · empty_authors 0 · missing_ci 0 · missing_tsv 0
```

## The incremental claim

`text_hash` is defined over **title and abstract only**. A title or abstract edit
re-embeds that paper; a category, date or version change skips it. That is the intended
trade-off, not an oversight.

A no-op rerun proves a skip path exists. It does not prove the path discriminates. This
does:

| measurement | code path | value |
|---|---|---|
| `n_changed` | embedding planner | **65** |
| `n_text_changed_papers` | silver build | **65** |

Two independent counters, different code, same announcement, same answer. On that run
4,206 papers were skipped because their searchable text was identical, and exactly 65 were
re-embedded because it had genuinely changed.

```
incremental embed  n_new 4,003 · n_changed 65 · n_unchanged 4,206 · n_to_embed 4,068
                   n_embedded 4,068 · n_failed 0 · 300.22 s · run embed-83d9b303f78c
```

The model is pinned by fingerprint (`2d51d6d165b7d00e`, revision `1110a243fdf4…`), so a
silent model upgrade appears as a ledger mismatch instead of quietly different vectors.

## Search

Three modes, one endpoint:

| mode | how | notes |
|---|---|---|
| `vector` | pgvector cosine over `paper_embeddings` | first query after a restart pays a model-load cold start |
| `keyword` | Postgres full-text, `paper_meta_fts_idx` | **1.02 ms** mean warm; `simple`, never `english` |
| `author` | `authors`, `authors_ci`, `authors_tsv` (3 GIN indexes) | ignores `q=` by design |

`total_matches` is the honest signal for author search: `null` means fewer rows than the
limit, an integer means the limit was hit. arXiv ships no author identifiers, so
`Wei Wang` returns ten papers by whoever shares that name and says ten.

Corpus facts: 23,445 author name entries, 19,854 distinct, mean 5.49 per paper, max 559
on a single paper.

## Design decisions

| decision | reason |
|---|---|
| Entries fingerprint, not page bytes | raw responses can embed request time; only entries are a valid idempotency measure |
| 144-hour lookback, 3.1 s spacing | arXiv visibility lags submission by up to ~78 h; config rejects anything under 3.0 s |
| Newest version wins in silver | otherwise a revision leaves a stale author list; measured at 6 gained / 1 lost |
| Circuit breaker at 5% bad records | fail the build and write nothing, rather than publish a partial corpus |
| `text_hash(title, abstract)` | the embedded text is the searchable text; metadata churn must not cost a vector |
| Model pinned by fingerprint | an unpinned upgrade is a silent change in every result |
| Authors kept out of the document `fts` | including them would change the stored column, the index and every keyword number |
| `simple`, never `english` stemming | English stemming turns `Harris` into `harri` and mangles accented names |
| Gate inside the DAG | a corpus that cannot answer author queries must fail at load, not at query time |
| No RRF / hybrid | evidence-gated out at this corpus size; retrieval quality is measured first |

## Verification

Everything quoted above was observed on the running system, and every number in
[EVIDENCE.md](EVIDENCE.md) carries the command that produced it. That file also lists the
claims the project **refuses** to make — read it before repeating any figure from this
README.

Five-minute re-verification:

```bash
curl -sS localhost:8090/health | jq '{ok, db, n_vectors}'
```

```bash
dc run --rm --entrypoint python airflow-scheduler -c "from docpulse.silver.load import _connect; c = _connect(); cur = c.cursor(); cur.execute('select (select count(*) from paper_meta) as papers, (select count(*) from paper_embeddings) as embeddings'); print(cur.fetchone())"
```

```bash
dc exec -T airflow-scheduler airflow dags list
```

## Known limitations

1. **No RAG, no generation.** Retrieval only; nothing writes an answer.
2. **No evaluation set yet.** No recall@k, no MRR. That is Day 8, before any generation.
3. **Quarantine has never fired on real data.** 0 of 13,000 entries were bad; the
   mechanism is proven by tests only.
4. **Vector latency is unmeasured.** 1.02 ms is the keyword path. HNSW is opt-in
   (`make -f day4.mk search-index`) and its presence has never been confirmed by `EXPLAIN`.
5. **Accents are not folded.** `Herve Panetto` will not find `Hervé Panetto`.
6. **Homonyms are unresolvable** — no author identifiers exist in arXiv metadata.
7. **No authentication.** Dev-grade: unauthenticated object store, default Airflow creds,
   single node. Not a deployment.

## Operational notes (learned the hard way)

- The live database is **`docpulse_embed`**, not `docpulse`. Pointing `psql` at the wrong
  one reports `paper_meta does not exist` while the API answers happily.
- **Container status says nothing about the database.** Postgres was down for 11 hours
  with `searchapi` `Up`; later two containers sat dead for 37 hours unnoticed. Policies are
  now `restart: unless-stopped` on postgres, objectstore, airflow-webserver and
  airflow-scheduler. `airflow-init` is a one-shot and stays at `no`; the embedder idles by
  design and only runs via `compose run --rm`.
- `./src` is bind-mounted **read-only** — `docker cp` into it fails. Edit the host file and
  it is already visible. New files also need `chcon -t container_file_t`.
- **Two images, two DB drivers.** The embedder ships psycopg 3, the Airflow image ships
  psycopg 2. All code goes through one `_connect()` that tries both.
- **Run S3-facing code through `dc run`, not the host venv.** `SignatureDoesNotMatch` from
  a host shell is a host credential or clock problem, not evidence the pipeline is broken.
- **Never `dc down -v` and never `docker volume prune`.** The volumes are the corpus.
- Disk was tight (92% used, 4.8 GB free), which is why the Airflow image deliberately has no
  torch. `docker builder prune` is the safe reclaim.

## Repository map

```
dags/                 the Airflow DAG
src/docpulse/         bronze, silver, embed, search packages
tests/                unit and DB-backed tests
contracts/            the YAML contract for silver
docker/               airflow and embed images
scripts/              one-shot migration and CLI entry points
EVIDENCE.md           every claim, with the command behind it
README_DAY7.md        the Day-7 narrative: what changed and why
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
