# Day 7 — from a script that works to a pipeline that runs

Days 1 to 6 built layers that each worked **when driven by hand**. The question Day 7
had to answer was narrower and harder:

> A paper arXiv announces tonight — does it end up searchable tomorrow with nobody
> typing a command?

The answer is yes, and the honest version of that answer is longer. Three things were
missing: the load step had no home in the DAG, the DAG's last task could not run in the
DAG's own image, and nothing on the box would notice if the whole thing stopped.

## What the corpus looks like now

```
papers            8,274
silver rows       8,274 across 60 months
embeddings        8,274      (8274, 8274) from paper_meta and paper_embeddings
quarantined       0
authors_fp        c2337d98dce34d6cbf4ae82dc4e7629d
```

Yesterday it was 4,271. The growth came from a real scheduled run, not a fixture:

```
ingest_to_bronze   21 pages · 4,200 entries · 4,075 in window · 10,418,609 bytes
                   stop_reason reached_window_start · behind_schedule False
build_silver       17,200 entries → 4,003 new · 196 revised · 65 text-changed · 0 quarantined
load_meta          8,274 rows written · 60 months
check_meta         papers 8274 · null_authors 0 · empty_authors 0 · missing_ci 0 · missing_tsv 0
```

## The load step gets a home

The loader existed only as a CLI. That is why the automated path stopped one layer
short of the database: the DAG could clean a corpus it could not publish. It became a
module with **one mapping** from silver rows to `paper_meta` columns, shared by the CLI
and the DAG, so the two paths cannot drift apart.

A fake-cursor harness caught a bug before it ever touched live data. The statement that
checks each column used `str.format()`, and Postgres type casts are written `'{}'` —
which `format()` reads as a replacement field. It raised `IndexError` on the first call.
They are now written `'{{}}'`. Two braces, caught by a fake, instead of a failed
production run.

## Two images, two database drivers

The first real run went green through four tasks and then died:

```
ModuleNotFoundError: No module named 'psycopg'
```

The embedder image ships **psycopg 3**. The Airflow image ships **psycopg 2** only.
Code that works in one container is not code that works in the pipeline, and no unit
test in a single-image venv can tell you the difference.

Both now go through one `_connect()` that tries psycopg 3 and falls back to psycopg 2.
One door for database access, and the next person adding a query inherits the fix
instead of rediscovering the bug.

## The incremental claim, tested against reality

Until this run, "incremental embedding" was proven only by a no-op rerun: embed
everything, embed nothing new. That proves the skip path exists. It does not prove the
skip path **discriminates**.

The run over new data:

```
n_rows_seen 8274 · n_new 4003 · n_changed 65 · n_unchanged 4206
n_to_embed 4068 · n_embedded 4068 · n_failed 0 · n_orphans_pruned 0
chars 6,212,694 · 300.22 s · run_id embed-83d9b303f78c
model_fp 2d51d6d165b7d00e
```

And the number that matters:

| measurement | code path | value |
|---|---|---|
| `n_changed` | embedding planner | **65** |
| `n_text_changed_papers` | silver build | **65** |

Two independent counters, different code, same answer. That is the difference between an
optimisation that is *asserted* and one that is *verified*: 4,206 papers were skipped
because their searchable text was identical, and exactly 65 were re-embedded because
their title or abstract had genuinely been edited.

## The DAG ends where its image can execute

The task after the gate was `verify_embeddings`, and it raised:

```
RuntimeError: sentence-transformers is not importable here … deliberately fatal
```

`build_encoder()` refuses to run without a real model rather than write meaningless
vectors. That refusal is the feature. The tempting repair — `EMBED_ENCODER=hash` — would
have turned the task green and the corpus useless.

So the DAG was trimmed to end at `check_meta`. **Six tasks, one chain, no task that
cannot execute in its own image.** Embeddings stay a deliberate `make embed` in the
image that owns the model, and the reason is written into the DAG beside the wiring so
it cannot be quietly re-added.

What the DAG guarantees is stated just as plainly: every `paper_meta` row is present and
passes the integrity gate. It does not guarantee a vector.

## The schedule, stated correctly

The ledger said `06:00 daily`. The DAG says:

```
schedule="@daily"          # 00:00 UTC
start_date=2026-10-01
catchup=False
max_active_runs=1
retries=2, exponential backoff
```

The ledger was wrong and now says what the source says. The correction came from
reading `dags/arxiv_bronze_ingest.py` after a stale `next-execution` timestamp made me
theorise a backfill storm that was never possible — `catchup` was already off.

The run history also tells a better story than the one I was telling:

```
scheduled__2026-10-29   success
scheduled__2026-10-30   success
scheduled__2026-10-31   success
```

The scheduler had been working for days. The chain simply ended at `verify_silver`, one
layer short of the database. Day 7 did not invent scheduling; it closed the gap between
"scheduled" and "searchable".

## Nothing noticed when it died

Two containers sat dead for **37 hours** while the box was suspended, and no one was told.
Three changes make that louder next time:

- `restart: unless-stopped` on **postgres, objectstore, airflow-webserver,
  airflow-scheduler**. Not on `airflow-init`, a one-shot migration container, and not on
  the embedder, which is idle by design and only ever runs through `compose run --rm`.
- `/health` already probed the database rather than returning a constant. It returns
  **503** with a hint when any check fails, and currently reports
  `ok: true, db: "up", n_vectors: 8274, model_matches: true`.
- The evidence ledger now records what is *not* automated, in the same table as what is.

## Facts worth keeping

- **`dc ps` says nothing about the database.** A container can be `Up` with a dead
  connection behind it. `/health` is the only answer that means anything.
- **`dc stats` at idle:** 1.1 GiB of 7.5 GiB across the whole stack, 8% CPU summed. The
  scheduler idles at ~5% by design. `searchapi` sits at ~110 MiB until the first vector
  query, because the encoder loads lazily — the first vector search is a cold start.
- **The embedding step belongs to the image that owns the model.** Do not add a hash
  encoder to make a task pass.
- **Run S3-facing code through `dc run`, not the host venv.** A `SignatureDoesNotMatch`
  from the host shell is a credential or clock problem on the host, never evidence that
  the pipeline cannot read the bucket. Three checks settle it in a minute: the keys in
  `.env`, `date -u` against the container, and a `find_silver_manifest_key` call from
  inside a container.

## What this does not claim

No LLM, no RAG answer layer, no evaluation set. Retrieval is the substrate; generation is
next. Quarantine has never fired on real data — the mechanism is proven by tests only.
HNSW is an opt-in whose presence has never been confirmed by `EXPLAIN`, so vector latency
is unmeasured; the 1.02 ms figure is the **keyword** path.

## Where Day 8 starts

A golden question set with expected paper IDs, scored as recall@k and MRR across the
three modes — before any generation is added. Evaluating retrieval first is what keeps a
bad answer attributable: with a scored retrieval baseline, you can say whether retrieval
or generation caused it. Without one, everything downstream is opinion.
