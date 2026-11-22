# Day 3 — incremental embeddings into pgvector (add-only patch)

This adds layer 3 of DocPulse: **hash-based incremental embeddings** of the silver
papers into Postgres + pgvector, verified independently, with a cost ledger.

It is written as an **add-only patch**. It does not overwrite your
`docker-compose.yml`, your `Makefile`, your `.env`, your `dags/`, your
`requirements-dev.txt` or your Airflow image. Every file it puts down is new,
except one optional line in the Makefile that you choose to add.

---

## 1. Four decisions I made for you, and why

**1. torch lives in its own image, not the Airflow one.**
You have 8.2 GB free on a 93%-used partition. Adding CPU torch +
sentence-transformers to the Airflow image takes it from ~1.2 GB to ~3.5 GB, and
every rebuild rewrites that layer. A separate one-off image (`docpulse-embedder`,
~1.1 GB) is only paid for once, and Airflow stays exactly as green as it is now.

**2. So embeddings run as a job, not as a DAG task (`make embed`).**
That is a real trade-off, not a hidden one: Airflow no longer orchestrates layer
3. The alternatives, and their costs, are written out at the top of
`optional/dags/embed_papers.py`. If you want it in the DAG, copy that file and
pick (A) fat image or (B) docker socket in the scheduler. Say the word and I'll do
it either way.

**3. A separate database (`docpulse`), not the Airflow metadata DB.**
Airflow owns and migrates its own schema. Pipeline tables in there work right up
until an Airflow upgrade runs `airflow db migrate` and you are afraid of two
things at once. `docker/postgres/init` scripts only run on a *first* init anyway,
so the schema is created by `make embed-db-init`, which is idempotent.

**4. Content hash decides what to re-embed — not the clock, not the row count.**
Silver already computes `text_hash` over title + abstract. A paper is re-embedded
only when that hash changes or the model changes. That is why this is called
*hash-based* incremental, and it is why a metadata-only revision costs nothing.

---

## 2. Preconditions

| Check | Command | Required |
|---|---|---|
| Day 1/2 green | `make ps`, Airflow shows 5 green tasks | yes |
| Free disk | `df -h /var` | **≥ 6 GB** (the image is ~1.1 GB; builds need headroom) |
| Git clean | `git status` | yes — commit before patching |
| Host tests pass | `make test` | yes — you should be at 122 |

If `git status` is not clean, commit first. The last four patches were applied
with `unzip -o`, which has no undo; this one is additive, but a commit is still
the only rollback you actually want.

---

## 3. Install

```bash
cd ~
unzip -o ~/Downloads/docpulse-day3.zip -d /tmp/day3
bash /tmp/day3/apply_day3.sh ~/docpulse --dry-run      # look at the plan first
bash /tmp/day3/apply_day3.sh ~/docpulse                # copies the new files
```

Then, once, on your host (no Docker needed for this step):

```bash
cd ~/docpulse
source .venv/bin/activate
PYTHONPATH=src:tests python3 -m pytest -q tests -k embed
```

**Checkpoint:** `79 passed, 6 skipped`. The 6 skipped are the Postgres tests;
they run in the container later. If any of the 68 fail on your machine I need the
first failure — that is almost certainly a name I guessed wrong from the
transcript.

Optional, if you want the targets merged into your own workflow:

```bash
bash /tmp/day3/apply_day3.sh ~/docpulse --force --patch-makefile
make embed-help          # instead of: make -f day3.mk embed-help
```

---

## 4. Run order

Do these one at a time, in this order. Every command is read-only except the last
three.

### Step 1 — preflight inside the scheduler (no torch needed)

```bash
make -f day3.mk embed-preflight
```

**This is the important step.** It checks all 7 assumptions this patch makes about
your Day 1/2 code and prints the **actual column names** of a real silver file. If
anything says `BLOCK`, paste the output and stop — it will be a one-line fix in
`src/docpulse/embed/compat.py`.

Look for:

```
A4 manifest key   silver/...  _manifest.json
A5 manifest       n_papers_total=4271 months=50 contract_version=1
A6 decode mode    pyarrow
   rows decoded   4271
columns in paper_month=....parquet (pyarrow):
    abstract, authors, categories, comment, doi, ..., text_hash, title, updated, version
mapped id         <- paper_id        (or arxiv_id / id — whatever yours is called)
mapped text_hash  <- text_hash
```

### Step 2 — build the embedder image (~1.1 GB, a few minutes)

```bash
make -f day3.mk embed-build
```

Check disk afterwards, `df -h /var`. If it drops below ~4 GB, stop and prune
before continuing.

### Step 3 — preflight inside the embedder (torch + psycopg live here)

The script prints `role core` or `role embedder` and only demands the packages that
role is supposed to have, so a missing `psycopg` in the scheduler is a NOTE, not a
failure.

```bash
make -f day3.mk embed-preflight-embedder
```

**Checkpoint:** `PASS torch`, `PASS sentence_transformers`, `PASS psycopg`,
`pgvector extension` OR a note saying it isn't installed yet (expected at this
point), and `postgres reachable`.

### Step 4 — create the database and tables

```bash
make -f day3.mk embed-db-init
```

**Checkpoint:** `database 'docpulse_embed' created` (or `already exists`) and
`schema ready: {'table': 'paper_embeddings', 'dim': 384, ...}`.

Why `docpulse_embed` and not `docpulse`: with `POSTGRES_DB` unset, the postgres
image names the database after `POSTGRES_USER`, which on your stack is `docpulse`
— so `docpulse` is almost certainly **Airflow's own metadata database**. Day 3
refuses to land there and says so (`REFUSING: the target database … is also the one
Airflow uses`) unless you pass `--allow-shared-db`.

### Step 5 — dry run: the plan, nothing written

```bash
make -f day3.mk embed-dry
```

**Checkpoint:** on your data expect roughly

```json
"plan": { "n_unique_papers": 4271, "n_new": 4271, "n_to_embed": 4271, "chars_to_embed": ~5500000 }
```

`n_to_embed == n_unique_papers` on the first ever run. Nothing is written:
`store.puts` is empty and no ledger row exists.

### Step 6 — the real run

```bash
make -f day3.mk embed
```

The first run downloads the model (~90 MB, once, into the `embed-model-cache`
volume) and embeds 4,271 abstracts on CPU. **Expect 1–3 minutes.** It prints one
JSON summary:

```json
{ "status": "success", "n_targets": 4271, "n_embedded": 4271, "n_failed": 0,
  "n_skipped": 0, "chars_total": ..., "seconds": ...,
  "model": {"model_id": "sentence-transformers/all-MiniLM-L6-v2",
            "revision": "1110a24...", "dim": 384, "fingerprint": "..."} }
```

Total vector storage is tiny: 4,271 × 384 × 4 bytes ≈ **6.6 MB**.

### Step 7 — run it again (the idempotency test you have done twice already)

```bash
make -f day3.mk embed
```

**Checkpoint:** `n_to_embed: 0`, `n_unchanged: 4271`, `n_embedded: 0`, and a new
`run_id` in the ledger with `status: success`. Nothing is re-embedded. This is the
real claim of Day 3, and it is cheap to demonstrate.

### Step 8 — independent verification

```bash
make -f day3.mk embed-verify
make -f day3.mk embed-status
```

**Checkpoint:** `"ok": true`, `n_rows: 4271`, `n_missing: 0`,
`n_stale_text_hash: 0`, `n_orphans: 0`, `deterministic: true`.

`embed-status` shows the run ledger: planned / embedded / skipped / failed /
chars / seconds per run.

### Step 9 — prove the incremental path on real new data

This is the check Day 2 never got. After arXiv's next announcement (Sun–Thu
20:00 ET, so ~01:00 UTC the next day, with a few hours of grace):

```bash
make trigger                                    # bronze + silver pick up new papers
make -f day3.mk embed-dry                       # what would be embedded?
make -f day3.mk embed                           # embed just those
```

**Checkpoint:** `n_to_embed` equals the number of **new + text-changed** papers
from the silver summary (`n_new_papers + n_text_changed_papers`), not 4,271.
That is the whole point of the layer, and it is the number to quote later.

### Step 10 — commit

```bash
git add -A && git commit -m "Day 3: hash-based incremental embeddings into pgvector"
git tag -a checkpoint-day3 -m "Day 3 complete: embeddings verified, incremental on real data"
```

---

## 5. What is verified, and what is not

Verified in my sandbox, on this code, today:

- 79 unit tests: encoder contract and fingerprints, configuration defaults, planning (new / changed /
  unchanged / model mismatch / missing text / duplicate ids / truncation),
  crash-safety (a mid-run failure keeps committed batches and the rerun resumes),
  model-change guard, rebuild, orphan pruning, all 8 verification checks, and the
  silver adapters.
- The pgvector SQL functions used (`vector_dims`, `vector_norm`) exist in the
  version pinned by `pgvector/pgvector:pg16`.
- The model revision is real: `1110a243…` is `main` of `all-MiniLM-L6-v2` as
  returned by the HF API on 2026‑11‑02.
- Package versions in `requirements-embed.txt` exist on PyPI today
  (sentence-transformers 6.2.0, torch 2.15.0, psycopg 3.3.6, pgvector 0.5.1).

**Not verified — no changes my claims:**

1. Anything about *your* code's names. That is exactly what preflight is for.
2. Real torch inference: I have no torch here, so 4,271 papers × MiniLM has never
   been run by me. The encoder interface is tested with a deterministic stand-in.
3. Real Postgres: the 6 DB tests skip without a database. They run in Step 4/8.
   Expect a SQL typo to surface there, not before.
4. The Docker build itself (no Docker here).
5. Semantic quality of the vectors. Nothing in this patch proves the embeddings
   are *useful* — only that they are deterministic, normalised, complete and
   fresh. That is a Day 4 question ("do nearest neighbours look sane?").

---

## 6. Rollback

```bash
git checkout -- Makefile          # only if you used --patch-makefile
git clean -fd src/docpulse/embed scripts tests docker/embed
git status                        # should be clean
docker rmi docpulse-embedder:local            # optional, frees ~1.1 GB
docker compose -f docker-compose.yml -f docker-compose.day3.yml down -v   # DROPS the model cache and the db volume? no: postgres volume is shared, do not use -v
```

Careful: **do not** run `docker compose ... down -v`. Your bronze/silver/Postgres
data lives in volumes and `-v` deletes them. To drop only Day 3's tables:

```bash
docker compose -f docker-compose.yml -f docker-compose.day3.yml run --rm --entrypoint psql embedder \
  "$DOCPULSE_PG_DSN" -c 'drop table if exists paper_embeddings, embedding_runs, embedding_models'
# (on the default settings that is the docpulse_embed database, not Airflow's)
```

---

## 7. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `sentence-transformers is not importable` | You ran `build` outside the embedder | `make -f day3.mk embed` (not `python -m …` on the host) |
| `ModelChangeError` | The table holds vectors from another model/revision/dim | Intended. `make embed-rebuild`, or set `EMBED_ALLOW_MODEL_CHANGE=1` |
| `LockBusy` | A previous run is still holding the advisory lock | `make embed-status`; if nothing is running, the connection died and Postgres released it — just re-run |
| `preflight` says `BLOCK docpulse.silver.build missing` | Wrong `PYTHONPATH` / wrong container | Run it via `make embed-preflight` (it uses the scheduler, where `src` is mounted) |
| `psycopg is not installed here` | `embed-db-init` run on the host | Run it via `make -f day3.mk embed-db-init` |
| `REFUSING: the target database … is also the one Airflow uses` | `DOCPULSE_DB` points at Airflow's metadata DB | Unset it (default `docpulse_embed`) or set a full `DOCPULSE_PG_DSN` |
| `permission denied` on `/opt/docpulse/src` | SELinux | The overlay already carries `,z`; confirm with `ls -Z` |
| `Could not connect ... port 5432` | You set `DOCPULSE_PG_PORT` from `POSTGRES_PORT` | Inside the container the port is **always** 5432; `dsn_from_env` deliberately ignores `POSTGRES_PORT` |
| Model download fails | No egress, or HF blocked | Pre-download on the host into the volume, or set `HF_HUB_OFFLINE=1` after one successful fetch |
| `n_missing > 0` in verify | A run was interrupted, or silver grew since | Re-run `make embed`; verify again |
| `n_orphans > 0` | Silver lost a paper (retention, or a bronze replay) | Left visible on purpose; `EMBED_PRUNE_ORPHANS=1 make embed` to delete them |
| `n_stale_text_hash > 0` | Silver changed after the last embed | Re-run `make embed`; if it persists, that is a real bug — send me the two lines |

---

## 8. Optional `.env` keys

None are required. Defaults are sane. Add only what you want to change:

```bash
# database
DOCPULSE_DB=docpulse_embed
# DOCPULSE_PG_DSN=postgresql://user:pass@postgres:5432/docpulse   # overrides everything
# DOCPULSE_PG_HOST=postgres
# DOCPULSE_PG_PORT=5432

# model
EMBED_MODEL_ID=sentence-transformers/all-MiniLM-L6-v2
EMBED_MODEL_REVISION=1110a243fdf4706b3f48f1d95db1a4f5529b4d41
EMBED_DIM=384
EMBED_NORMALIZE=true
EMBED_BATCH_SIZE=32
EMBED_MAX_CHARS=4000
EMBED_ENCODER=auto              # auto | hash | sentence-transformers

# safety switches (leave off)
EMBED_ALLOW_MODEL_CHANGE=false
EMBED_PRUNE_ORPHANS=false
```

`EMBED_ENCODER=hash` is for plumbing checks only: it produces deterministic but
*semantically meaningless* vectors, and it is refused silently by nothing — the
fingerprint differs, so switching to it later triggers the model-change guard.
That is deliberate.

---

## 9. Handoff to Day 4

What Day 4 (search / RAG) needs that now exists:

- `paper_embeddings(paper_id, version, text_hash, model_fp, embedding vector(384), chars, embedded_at)`
- an exact-scan cosine query (no index needed under ~100k rows):
  ```sql
  select paper_id, 1 - (embedding <=> %s::vector) as score
    from paper_embeddings
   order by embedding <=> %s::vector
   limit 10;
  ```
- `embedding_runs` as the cost ledger, and the manifest at
  `gold/embeddings/_manifest.json` as the "layer complete" flag.

Two decisions Day 4 will force, both flagged in `CONFLICTS_AND_FIXES.md`:

1. **Silver is partitioned by `published` month**, so a 2019 paper revised today
   lives in `2019-05`. A "recent papers" filter must use `updated`, not
   `published`, or the demo will quietly show stale results.
2. At 4,271 vectors an HNSW index is slower to build than it is worth. Add it
   when you pass ~100k rows, not before.
