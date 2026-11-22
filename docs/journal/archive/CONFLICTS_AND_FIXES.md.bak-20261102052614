# Conflicts, issues and improvements — everything I found, and what to do

This has four parts:

- **A.** Conflicts between this Day 3 patch and your current checkout, each with the resolution I chose.
- **B.** Problems that already existed and that Day 3 now makes worse or more expensive.
- **C.** Things still open from the Day 1/2 transcript.
- **D.** What could be done better, ranked.
- **E.** What I need from your side.

Severity: **H** = will bite you, **M** = costs time, **L** = cosmetic.

---

## A. Conflicts between this patch and your directory

| # | Conflict | Sev | How this patch handles it |
|---|---|---|---|
| A1 | **torch does not fit in the Airflow image.** 8.2 GB free, 86% used. CPU torch + sentence-transformers takes that image from ~1.2 GB to ~3.5 GB, and rebuilds rewrite that layer twice per build. | **H** | Separate `docpulse-embedder:local` image (~1.1 GB), built once, never touched by `make up`. |
| A2 | **`docker-compose.yml` now carries two hand edits of yours**: the SELinux `,z` labels on 4 mounts, and the SeaweedFS `-master.volumeSizeLimitMB=256 -volume.max=30` flags. A patch that overwrites that file silently undoes both — and the SeaweedFS one is the difference between a working object store and `InternalError` on every write. | **H** | Compose **overlay** file (`docker-compose.day3.yml`), merged with `-f base -f overlay`. Your file is never written to. |
| A3 | **Your Makefile is hand-tuned** (`exec -T` after fix3, `set -a; . ./.env` for smoke, `PYTHONPATH=src:tests`). Overwriting it loses that. | **M** | `day3.mk`, used as `make -f day3.mk …` or via one appended line `-include day3.mk` (opt-in, backed up). No target-name collisions — checked against init, test, smoke, up, ps, logs, down, reset, backfill, trigger, digest, silver, silver-dry. |
| A4 | **Airflow parses every file in `dags/`.** With `DAGS_ARE_PAUSED_AT_CREATION=False` (which your setup must have, since `arxiv_bronze_ingest` started unpaused), a new DAG file would also start running immediately — and it would try to import torch, fail, and add noise to a DagBag you have already fought once. | **M** | The optional DAG lives in `optional/dags/`, which Airflow never reads, and it defines no DAG unless `DOCPULSE_EMBED_IN_AIRFLOW=1`. |
| A5 | **Your Postgres instance belongs to Airflow.** Airflow owns and migrates its own schema; a `pgvector` table dropped in there is one `airflow db migrate` away from a bad afternoon. Also, `/docker-entrypoint-initdb.d` only runs on a *first* init, so it cannot be used now — your volume already exists. | **M** | New database `docpulse` in the same server, created by an idempotent `make embed-db-init`. Airflow's DB untouched. |
| A6 | **`POSTGRES_PORT` in your `.env` is the host-side mapping** (you were told to set 5433 if 5432 was taken). Reusing it inside a container sends you to a closed port and looks like "Postgres is down". | **M** | `dsn_from_env()` deliberately ignores `POSTGRES_PORT`; it uses `DOCPULSE_PG_PORT`, default 5432. Documented in the config docstring. |
| A7 | **Your `.env` is gitignored and holds your real values**; a patch that ships `.env` changes either does nothing or clobbers secrets. `docpulse-fix2` needed a manual `sed` for `ARXIV_LOOKBACK_HOURS` for exactly this reason. | **L** | Every Day 3 key is optional with a safe default. They are listed in `README_DAY3.md` §8 for you to add only if you want to change one. |
| A8 | **Test-suite collisions.** A `conftest.py` or fixture file would be overwritten by, or overwrite, whatever Day 1/2 already has, and could silently change how your 122 tests are collected. | **M** | No `conftest.py`. Test doubles live in `tests/_day3_fakes.py` (not collected by pytest or `unittest discover`, because of the leading underscore). Tests are `unittest` classes, so both of your `make test` paths run them. |
| A9 | **Two images means two dependency sets that can drift** — the scheduler has pyarrow/boto3, the embedder has its own copies. | **M** | The embedder pins the same libraries in `requirements-embed.txt`, and verification runs inside a container that shares your mounted `src`, so both read the same code. Drift is possible; it is not silent, because verify compares against silver each run. |
| A10 | **`compose run` with the overlay starts only what it declares.** If only `postgres` is declared, a run against a stopped stack fails on the silver read with a confusing connection error, not "objectstore is down". | **M** | The overlay declares `depends_on: postgres (healthy)` **and `objectstore`**, matching the health-check discipline of your base file. |
| A11 | **The overlay re-declares volumes.** If the project name changed (e.g. a `COMPOSE_PROJECT_NAME` in `.env`), `compose run` could see an empty Postgres and you would "lose" your data. | **M** | The overlay adds only one named volume (`embed-model-cache`) and never re-declares yours. Always invoke it as `-f docker-compose.yml -f docker-compose.day3.yml`, in that order — the first file sets the project. `day3.mk` does this. |
| A12 | **Mounts need SELinux labels.** You are on Tumbleweed with SELinux enforcing (you applied `,z` in October). A new container mounting `./src` without `,z` sees `Permission denied` and it looks like a code bug. | **M** | All four new mounts carry `:ro,z`. |
| A13 | **I could not see your repo.** Column names (`paper_id` vs `id` vs `arxiv_id`), the manifest key, the codec API, and the classmethod names are all *inferred from the transcript*, e.g. `MANIFEST_KEY`, `paper_fields`, `ParquetCodec`, `S3Store.from_settings`, `load_contract`, `default_contract_path`. | **H** | `src/docpulse/embed/compat.py` isolates every guess in one file with fallbacks, and `make embed-preflight` **prints your actual column names** and fails loudly with an actionable message. One mismatch = one small edit. |
| A14 | **`make up` must stay exactly as green as it is now.** A new service in the stack is a new way to break it. | **L** | `embedder` has `restart: "no"` and `command: sleep infinity`; it is never in your base compose file, so `make up` cannot start it. If you ever do `-f base -f day3 up`, it idles at ~1 MB RAM. |

---

## B. Problems that already existed, and that Day 3 now makes worse

### B1. You are about to embed text nobody has ever looked at — **H**

This is the one I would fix first. `n_quarantined: 0` on 13,000 real entries proves
fields are present and well-formed. It does **not** prove `title` holds a title, or
that `abstract` is not actually `comment`. Verification cannot catch a *swapped*
mapping — every rule you have would still pass. Day 2 could live with that; Day 3
turns it into 4,271 vectors that are silently meaningless, and Day 4 then builds
search on top.

**Do this before `make embed`** (30 seconds, container):

```bash
docker compose exec -T airflow-scheduler python - <<'EOF'
import json
from docpulse.config import StorageSettings
from docpulse.storage import S3Store
from docpulse.silver.build import MANIFEST_KEY
from docpulse.silver.codecs import ParquetCodec
from docpulse.silver.contract import load_contract
from docpulse.silver.build import paper_fields
from docpulse.silver.tasks import default_contract_path
s = S3Store.from_settings(StorageSettings.from_env())
codec = ParquetCodec(paper_fields(load_contract(default_contract_path())))
m = json.loads(s.get_bytes(MANIFEST_KEY))
rows = [r for x in m["months"] for r in codec.decode(s.get_bytes(x["key"]))]
for r in rows[:3]:
    print({k: (str(v)[:80] + "...") if len(str(v)) > 80 else v
           for k, v in r.items() if k in ("paper_id","id","arxiv_id","title","abstract","published","updated","version","pdf_url","categories","authors")})
print("rows:", len(rows))
EOF
```

If those three look like real papers, you are clear. If a field is obviously in the
wrong place, that is a Day 2 bug worth more than everything else on this page.

### B2. Silver is partitioned by `published` month — and Day 4 will query "recent" — **M**

50 files, ~85 rows each. A paper published in 2019 and revised yesterday lives in
`2019-05/`. So:
- "recent papers about X" must filter on `updated`, not on the month folder;
- a naive folder-based filter will quietly return stale results in your demo;
- 85 rows per file is below the point where a folder partition helps at all.

Not Day 3's problem, but Day 3 sits right on top of it. Decide before Day 4:
year partitions, or partition by `updated` month, or one file + a `month` column.

### B3. Bronze publish is still not atomic — **H** (unchanged by this patch)

`clear → write pages → write manifest`. A crash or disk-full between the first two
destroys a partition that was good. It only *looked* safe on Oct 29 because the
partition was empty. Day 3's manifest is written **last and only on success**, which
is the pattern bronze should adopt: make the manifest the commit record, and treat
objects not listed in a valid manifest as unpublished.

### B4. `text_hash` semantics are unverified — **M**

Incrementality is only as correct as that hash. If Day 2's `text_hash` were
computed over, say, id + title only, then abstract-only revisions would never
re-embed, and nothing would tell you. Cheap test (no change to your code):

```bash
docker compose exec -T airflow-scheduler python - <<'EOF'
import hashlib
# ... same loading preamble as B1 ...
match = sum(1 for r in rows[:500]
            if r.get("text_hash") == hashlib.sha256(
                (r.get("title","") + "\n\n" + r.get("abstract","")).strip().encode()).hexdigest())
print(f"{match}/{min(500,len(rows))} rows match sha256(title + '\\n\\n' + abstract)")
EOF
```

100% means silver uses the same composition I do (nice, then hashes are portable).
0% means a different composition — harmless, because Day 3 always trusts silver's
hash and only uses its own composition for the *text*. **Anything in between is a
red flag** and worth a look. Day 3 does not depend on the answer; it just makes the
answer cheap to get.

### B5. Duplicate paper ids in silver would corrupt an id-keyed embedding table — **L**

Your `verify_silver` checks ids are unique and sorted; the planner still dedupes
defensively (highest version wins) so a silver regression cannot double-embed or
store a stale version. Covered by a unit test.

### B6. 68 new tests that nothing runs automatically — **M**

No CI, no `ruff`, no pre-commit across 5 days of work. Every patch so far was
validated by hand. Ten lines of GitHub Actions (`make test` on push) would have
caught nothing so far — and will catch the next thing.

### B7. Disk: 8.2 GB free with ~45 GB unaccounted for — **M**

`du -xh --max-depth=1 /` and `snapper list` were requested twice and never sent.
The embedder adds ~1.1 GB image + ~90 MB model. That is affordable *now*; the next
big thing (a reranker, a larger model) will not be.

---

## C. Still open from Day 1/2

| # | Item | Why it matters |
|---|---|---|
| C1 | `git commit`/`git tag` were instructed 14 and 6 times, and their output was never shown | If `checkpoint-day1`/`checkpoint-day2` do not exist, you have no rollback point and 5 patches applied with `unzip -o` |
| C2 | `ruff` promised on Day 1, never run | My code is written to ruff defaults; if your config is stricter, `make embed-test` may flag style in `src/docpulse/embed/` |
| C3 | Airflow base image tag never pinned | `pgvector:pg16` and SeaweedFS (digest) are pinned; the image that runs everything is not |
| C4 | No lockfile with hashes (`requirements-dev.txt` is ranges) | Two `pip install` runs a month apart can differ; relevant now that vectors depend on a tokenizer version |
| C5 | `verify_bronze` XCom numbers never observed | Superseded by the digest line, but the XCom path itself is unproven |
| C6 | No retention or compaction for bronze | 144 h lookback ⇒ ~8 MB/day, ~2.9 GB/year; no rule for pruning snapshots |
| C7 | The 30,000-result API ceiling caps history at ~67 days | Not fixable by paging; needs OAI‑PMH or the bulk S3 snapshot |
| C8 | README drift | Already corrected twice in the log ("6 h lookback", "silver must filter by manifest window"); it will drift again |

---

## D. What could be done better, ranked

**P0 — before `make embed`**
1. **Look at 3 real silver rows** (B1). Everything downstream depends on it.
2. **Free disk to ≥10 GB** and send `du`/`snapper` output once, so this stops being a recurring tax.
3. **Commit and tag** so the patch stack has a floor (C1).
4. **Run the preflight before the real run** — it is the only thing standing between my guesses and your column names.

**P1 — this week**
5. **Manifest-as-commit-record** for bronze (and check silver's month writes are atomic too) (B3).
6. **Watermark lookback**: steady-state runs should fetch 2–3 pages, not 16, and 67% of bronze stops being duplicate payload.
7. **CI + ruff**, 20 minutes total.
8. **Pin the Airflow image + generate a lockfile** — worth more now that a tokenizer version quietly changes your vectors.

**P2 — before Day 4**
9. **Revisit the silver partition key** against the Day 4 query (B2).
10. **OAI‑PMH source** for history, since the Atom API cannot go back past ~67 days (C7).
11. **Retention** for bronze; **alerts** on `quarantined > 0`, `behind_schedule`, free disk < 15%.
12. **HNSW index**: not yet. Exact scan is faster at 4,271 rows. Add past ~100k.

---

## E. What I need from your side

**Blocking (so Day 3 runs at all)** — paste the output of these, in order:

1. `bash apply_day3.sh ~/docpulse --dry-run` (the plan; confirms nothing collides)
2. `make -f day3.mk embed-preflight` — full output, especially the **columns list** and any `BLOCK` line
3. `make -f day3.mk embed-preflight-embedder` — full output
4. `PYTHONPATH=src:tests python3 -m pytest -q tests -k embed` — last 20 lines

**Strongly wanted (removes all remaining guessing)** — any of:

5. Your repo as a zip, or just these five files:
   `docker-compose.yml`, `Makefile`, `contracts/silver_papers.yml`,
   `src/docpulse/silver/build.py`, `src/docpulse/storage.py`, `src/docpulse/config.py`.
   With those I can delete `compat.py`'s fallbacks and write against the real API.
6. `git log --oneline -8 && git tag` — to close C1.
7. `findmnt -no FSTYPE /var; df -h /var; sudo du -xh --max-depth=1 / 2>/dev/null | sort -h | tail -8`
8. The 3 real silver rows from B1 (redact nothing; they are public papers).

**Decisions only you can make:**

9. **Airflow integration**: Option A (`make embed` from cron/systemd — my default,
   zero risk to your green DAG) or Option B (in the DAG, needs a fatter image or a
   docker socket). Tell me which and I will finish it properly.
10. **Scheduling**: what time should `make embed` run relative to arXiv's
    announcement (Sun–Thu 20:00 ET ⇒ ~01:00 UTC)? My suggestion: 05:00 UTC,
    which gives the API four hours to catch up, exactly like your DAG's window.
