# Day 4 — search API

Semantic search over the vectors Day 3 built. One process, three tables' worth of
state, no new image.

## Why it reuses the embedder image

Query encoding needs the *same* model that embedded the corpus, and that model
(torch, ~1 GB) lives only in `docpulse-embedder:local`. A separate API image would
cost another ~3 GB for no isolation benefit — both processes read the same
`paper_embeddings` table. So Day 4 adds `fastapi` + `uvicorn` to
`requirements-embed.txt` and runs a second container from the same image.

Cost: **one image rebuild** (the pip layer, ~60 s, layers below stay cached).

## Data model

| Table | Written by | Holds |
|---|---|---|
| `paper_embeddings` | Day 3 | vector, `text_hash`, `model_fp`, `arxiv_id` |
| `paper_meta` | `day4_load_meta.py` | title, abstract, categories, published, updated |

Titles live in Postgres rather than parquet so a query is a single round trip.
`paper_meta` is loaded from silver and is idempotent: re-running updates in place,
`--prune` removes papers that vanished from silver.

## Run it

```bash
make -f day4.mk search-build      # once: rebuilds the shared image with fastapi/uvicorn
make -f day4.mk search-meta       # load paper_meta from silver (~4k rows, seconds)
make -f day4.mk search-up         # http://localhost:8090
make -f day4.mk search-smoke      # curl /health and one real query
```

Or step by step, if you prefer to see each stage:

```bash
docker compose -f docker-compose.yml -f docker-compose.day3.yml -f docker-compose.day4.yml \
  run --rm searchapi python /opt/docpulse/scripts/day4_load_meta.py --dry-run
```

## Endpoints

| Route | Answers |
|---|---|
| `GET /` | one-page UI (self-contained, no CDN) |
| `GET /search?q=&limit=&category=&since=&until=&include_abstract=` | cosine-ranked papers; `score` is similarity in `[-1, 1]` |
| `GET /papers/{arxiv_id}` | one paper + whether it has a vector |
| `GET /health` | `200` when healthy, `503` with a `hint` when not |
| `GET /stats` | row counts, query-cache hit rate, effective settings (password redacted) |

`limit` is clamped to `SEARCH_MAX_LIMIT` (default 100) instead of erroring — a
client asking for 10,000 gets 100, not a 422.

`/health` is deliberately strict: it fails if the table is empty, if the stored
`model_fp` differs from the model this process encodes with, or if `paper_meta` is
missing. A search API that answers with `null` titles is broken, not degraded.

## Configuration

All optional; defaults are correct for this stack. Put them in `.env`.

| Variable | Default | Meaning |
|---|---|---|
| `SEARCH_PORT` | `8080` | port inside the container |
| `SEARCH_PUBLISH` | `8090` | port on the host (8080 belongs to Airflow's webserver) |
| `SEARCH_DEFAULT_LIMIT` | `10` | results when `limit` is omitted |
| `SEARCH_MAX_LIMIT` | `100` | hard cap |
| `SEARCH_CACHE_SIZE` | `512` | distinct query vectors kept in memory (`0` disables) |
| `SEARCH_TABLE` | `paper_embeddings` | Day 3's table |
| `SEARCH_META_TABLE` | `paper_meta` | metadata table |

Model, dimension and DSN are **not** Day 4 settings: they come from Day 3's
`EmbeddingSettings`, so the two services cannot drift apart.

## What is deliberately not here

- **Auth / rate limiting.** It binds `0.0.0.0:8090` on your host. Fine on a laptop;
  put it behind a proxy before it faces anything else.
- **Pagination / offsets.** Cosine ranking has no stable order past the cut, so
  "page 2" is a different query, not a continuation.
- **Hybrid keyword search.** Postgres full-text over `paper_meta.abstract` is the
  natural next step (it fixes exact-match queries that embeddings handle poorly,
  like "Yann LeCun" or a dataset name).
- **The HNSW index.** Sequential scan is faster below ~100k rows. When you cross
  that, `make -f day4.mk search-index` creates it — measure before and after,
  because the metadata filters can stop Postgres from using it.

## Tests

```bash
make -f day4.mk search-test        # in the container: 34 tests, no torch/DB needed
PYTHONPATH=src:tests python3 -m pytest -q tests -k search    # on the host, API tests skip
```

The API tests inject a fake database and a hash encoder, so they exercise routing,
validation, clamping, error handling and the cache without Postgres or a model.
They skip cleanly on a host venv without `fastapi`.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `/health` → 503, hint mentions `embed` | Day 3 produced no vectors |
| `/health` → 503, hint mentions `search-meta` | `paper_meta` empty — run `make -f day4.mk search-meta` |
| `/health` → 503, hint mentions fingerprint | table built by a different model — re-embed or fix `EMBED_MODEL_ID` |
| `/search` → 503 `database unavailable` | Postgres down, or wrong password in the DSN |
| results with `"title": null` | `paper_meta` loaded for different ids than the vectors |
| container exits at once, `ModuleNotFoundError: fastapi` | image not rebuilt — `make -f day4.mk search-build` |
| `Bind for 127.0.0.1:8080 failed: port is already allocated` | Airflow's webserver owns 8080; use the default 8090 or `SEARCH_PUBLISH=####` |
| `/health` returns Airflow's scheduler JSON | you are curling port 8080 — the API is on 8090 |
