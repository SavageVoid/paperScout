# DocPulse — checkpoint after Day 6

State of the project at tag `day6`. Everything below was measured on this machine
or verified in a sandbox carrying the same source.

**Resume here:** author search works. The next useful step is deciding whether
`author=` should intersect with `q=` instead of overriding it, then Day 7 —
whatever that is — starts from a green tree: 385 passed, 15 skipped, ruff clean.

---

## 1. Where the system stands

| layer | state |
|---|---|
| Ingest | arXiv OAI-PMH → bronze (SeaweedFS, 69 objects, 13,000 records) → silver |
| Corpus | **4,271 papers** in database **`docpulse_embed`** — *not* `docpulse` |
| Embeddings | `all-MiniLM-L6-v2 @ 1110a24`, `max_df` 10% = 427, image `docpulse-embedder:local` |
| Search — vector | pgvector cosine, `mode=vector` (default), unchanged since Day 4 |
| Search — keyword | stored weighted `fts` + GIN, `mode=keyword` (Day 5) |
| Search — author | `authors` / `authors_ci` / `authors_tsv` + 3 GIN indexes (Day 6) |
| API | FastAPI on host **8090**, `/health`, `/search?q=|author=&mode=&sort=&min_score=` |
| Tests | **385 passed, 15 skipped** |
| ruff | **clean** |

## 2. Day 6 in one paragraph

Three nullable columns and three GIN indexes on `paper_meta`, backfilled from the
bronze harvest with the newest version of each paper winning. An author arm sits
beside vector and keyword: exact case-insensitive containment first, a `simple`
tsvector fallback only when exact finds nothing, newest first. `/search` gains
`author=`, `q` becomes optional, and the response reports which arm answered.

## 3. The numbers

| measurement | value |
|---|---|
| bronze objects read | 69 |
| records parsed | 13,000 |
| papers | 4,271 |
| duplicate records | 8,729 (3.04× per paper) |
| papers changed by using the newest version | **6 gained authors, 1 lost** |
| name entries / distinct | 23,445 / 19,854 |
| authors per paper | mean 5.49, median 4, max **559** |
| non-ASCII names | 542 |
| braced accents folded | 8 |
| structural gates | null authors **0**, empty array **0**, authors-without-tsv **0**, authors-without-ci **0** |
| acceptance | `Yann LeCun` **1** (`2610.39302`), `Geoffrey Hinton` **1** |
| index use | all three GIN indexes chosen by the planner, no seqscan forcing |
| Day-5 keyword probe | **identical table** — `10/12`, sizes `[1,1,2,10,…]`, `3/10` |

## 4. API contract

```
GET /search?q=...&author=...&mode=vector|keyword&sort=relevance|updated&min_score=0..1
```

| case | behaviour |
|---|---|
| `author=` given | author arm; `mode: "author"`; `author: {mode, key, total_matches}` |
| `author=` **and** `q=` | author wins, `q` ignored, reported honestly |
| neither | **422** |
| `q="   "` alone | **422** |
| `min_score` with `author=` | ignored |
| `mode=keyword` with `author=` | author wins |
| any mode | `authors` present on every result row |

Exact hits are ordered newest-first; there is no relevance score because every
exact hit matches equally. `total_matches` appears only when the limit is reached.

## 5. Verify in five minutes

```bash
cd ~/docpulse && source .venv/bin/activate
PYTHONPATH=src python -m pytest -q                      # 385 passed, 15 skipped
make -f day5.mk keyword-probe                           # must match the Day-5 table
curl -s 'localhost:8090/search?author=Yann+LeCun' | jq '{mode, n: .n_returned, author}'
curl -s 'localhost:8090/search?author=lecun' | jq '{author_mode: .author.mode}'    # partial
curl -s 'localhost:8090/search?author=Wei+Wang' | jq '.author'                     # total_matches 10
curl -s -o /dev/null -w '%{http_code}\n' 'localhost:8090/search'                    # 422
```

## 6. Decisions, and what would reverse them

| decision | evidence | reverse if |
|---|---|---|
| Authors in their own columns, not in `fts` | Day-5 probe would otherwise be invalidated | users expect `q=LeCun` to work in keyword mode |
| Newest version of each paper wins | 6 papers gained authors, 1 lost | — |
| `simple` config for names | `english` stems `Harris` → `harri` | never |
| `authors_ci` as a separate column | `@>` is case-sensitive; phrase matching can straddle authors | never |
| Partial only as fallback, never padding | mixing exact and partial in one list is unreadable | — |
| Newest-first, no relevance score | every exact hit matches equally | first-author ranking is wanted |
| No disambiguation | arXiv has no author identifiers | an external ID source appears |
| `author=` wins over `q=` | one unambiguous claim per request | intersection proves more useful in practice |

## 7. Known issues

1. **Accents are not folded** — `Herve Panetto` misses `Hervé Panetto`. Needs
   `unaccent` plus a decision about display names.
2. **Homonyms are unresolvable**; `total_matches` is the honest signal.
3. **No first-author ranking**; `authors` keeps order, `authors_ci` does not.
4. **559-author paper** will render badly unless the response trims it.
5. **`author=` ignores `q=`** — deliberate, documented.
6. **Keyword probe wording** was corrected to stop claiming there is no authors
   column; `q=LeCun` returning nothing in keyword mode is by design.

## 8. Operational traps (all hit this week)

- Papers live in **`docpulse_embed`**, not `docpulse`. Wrong database =
  `paper_meta does not exist` while the API keeps answering.
- **`./src` is mounted read-only** — `docker cp` into it fails by design. Edit the
  host file; the container already sees it.
- **New files need an SELinux label** (`chcon -t container_file_t`) or the
  container gets `EACCES` on a 644 file.
- **Plain columns can't use `STABLE` functions** in `GENERATED ALWAYS`
  (`array_to_string`). Fill them in the `UPDATE` instead.
- **`check` is a reserved word** — `AS check` fails; use `AS gate`.
- **A healthy container proves nothing about its database.** Postgres was down 11
  hours while `searchapi` was `Up` and `/health` returned 200.

## 9. File map

```
src/docpulse/search/authors.py       engine: normalise(), EXACT_SQL/PARTIAL_SQL, search_author()
src/docpulse/search/db.py            + SearchDB.search_author() (patch, applied)
src/docpulse/search/api.py           + author=, q optional, author branch (patch, applied)
scripts/day6_authors_probe.py        reads bronze from the store; coverage and name shapes
scripts/day6_add_authors.py          extracts to TSV; --dry-run and --emit
scripts/day6_add_authors.sql         DDL + COPY + UPDATE + gates; re-runnable
scripts/day6_patch_db.py             idempotent, applied
scripts/day6_patch_api.py            idempotent, applied
tests/test_authors.py                24 tests
README_DAY6.md                       the long-form rationale
```

## 10. Next

1. Decide `author=` vs `q=` interaction (intersect or override).
2. Accent folding, if users actually type unaccented names — measure first.
3. Trim long author lists in the response.
4. Anything Day 7 asks for; the corpus and search are both healthy.
