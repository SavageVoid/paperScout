# Day 5 — keyword search as a second mode

Vector search answers "which papers are near this in meaning". It cannot answer
"which papers are *about* this", because a paper can sit close to a query in
embedding space while never using the words — and the reverse. Day 5 adds a full
text arm beside the vector one and leaves the choice to the caller.

Nothing here replaces the vector path. `mode` defaults to `vector`, and a request
that doesn't mention it behaves exactly as it did on Day 4.

## What was built

- **A stored, weighted `fts` column** on `paper_meta` — title `A`, abstract `B` —
  with a GIN index (`paper_meta_fts_idx`) and an index on `updated`. Stored, not
  computed per row, so the query plan uses the index instead of re-parsing
  vectors 4,271 times.
- **A keyword engine** (`src/docpulse/search/keyword.py`) that runs a strict
  AND query first and falls back to a coverage-ordered OR only when the strict
  query returns nothing.
- **A policy layer** (`src/docpulse/search/modes.py`) holding the pooling,
  ordering and scoring rules, kept out of the SQL so it can be tested
  without a database.
- **`/search` gains three parameters**: `mode`, `sort`, `min_score`, plus
  per-mode diagnostics in the response.

## Measured, not assumed

Corpus: 4,271 papers, Postgres 16.15, `all-MiniLM-L6-v2 @ 1110a24`,
`max_df` 10% = 427.

`make -f day5.mk keyword-probe`:

| metric | value |
|---|---|
| strict answered | 10/12 queries |
| answer set sizes | `[1, 1, 2, 10, 10, 10, 10, 10, 10, 10]` |
| answers with ≤ 8 rows | 3/10 |
| strict mean / max | 1.86 ms / 2.81 ms |
| no match | 2 — "Yann LeCun", "Geoffrey Hinton" (no `authors` column yet) |
| plan for a selective query | Bitmap Index Scan on `paper_meta_fts_idx`, 0.26 ms, chosen with `enable_seqscan` both on and off |

Title-relevance by query: diffusion / RAG / quantization / RLHF 5/5, LoRA 4/5,
attention 3/5, protein 3/5, CMB 1/1, sparse attention 0/2, BERT 0/1.

**The number that shaped the design: 7 of 10 strict answers hit the limit
exactly.** A strict AND on a small corpus either finds one paper or finds forty.
That is why both modes exist and why the response reports `n_exact` — at this
size the useful signal is often "this query is rare" versus "this query is
common", not the ranking.

## The demonstration

`q="BERT pretraining masked language model"`:

- `mode=vector` — 10 papers, the first few being prose neighbours that discuss
  pretraining without being the BERT paper.
- `mode=keyword` — **1 paper**, `2610.31723`, `mode: strict`, `n_exact: 1`,
  all five stems matched.

Words present is not the same as the paper being about it, and now the response
says which of the two it gave you.

## API

```
GET /search?q=...&mode=vector|keyword&sort=relevance|updated&min_score=0.0..1.0
```

- `mode=keyword` never calls the encoder — no vector is needed, so asking the
  model for one would only add latency and a failure mode.
- `sort=updated` re-orders the most similar rows, newest first, undated last. It
  does **not** scan the corpus by date: a date sort that ignores the query isn't
  a search. It widens the candidate pool first (`10×limit`, min 100, max 1000),
  then trims back to `limit`.
- `min_score` is opt-in and applies to the vector arm only. A cosine floor means
  nothing to a `ts_rank_cd` score. Useful band observed: 0.28–0.50.
- An unknown `mode` or `sort` is a **422**, not a silently ignored parameter —
  which is also how you tell a stale process from a live one.
- The response always states `mode`, `sort`, `min_score`, and a `keyword` block
  (`mode`, `stems`, `n_exact`, `n_returned`, `coverage`) or `null`.

## Decisions and the evidence behind them

- **Strict first, then a coverage-ordered OR.** Not blended: a single ranked list
  from both arms makes the two failure modes indistinguishable to the caller.
- **A short strict list is never topped up from the relaxed tail.** If strict
  returned 2, the answer is 2. Topping up would silently mix "exact" and "partial"
  rows under one label.
- **No `min_score` default.** Filtering by default hides recall loss behind a
  number the caller never set.
- **No fusion, no RRF, and `mode=hybrid` was not built.** At 4,271 papers, RRF
  trades a readable answer for a blended one and buys nothing measurable. Revisit
  around ~50k papers, with a probe that shows the blended list beating both arms.
- **English stemming is not idempotent.** `sparse → spars → spar`: running a
  lexeme through the dictionary twice silently searches for a term no document
  contains. Queries are built as a literal `'a' & 'b'::tsquery` from stems the
  engine computed itself, never re-parsed by `to_tsquery`, and a test asserts
  that no SQL arm contains `to_tsquery` at all.

## Known gaps

- **No `authors` column.** Author queries return nothing, by construction. Day 6.
- **Seven of ten probe queries saturate the limit.** The strict arm is a filter,
  not a ranker; ordering within a saturated answer is coverage then `ts_rank_cd`.
- **English stemming only.** Non-English abstracts stem badly and silently.
- **Sequential scan is still correct.** At 4,271 rows the index wins on selective
  queries and the planner is left to choose; revisit at ~100k.

## Re-measuring

```
make -f day5.mk fts-probe        # what the stored column and index do
make -f day5.mk rank-probe       # ts_rank_cd behaviour, IDF absence
make -f day5.mk keyword-probe    # the numbers in the table above
```

Any change to the engine or the SQL should move these deliberately. If a patch
to `db.py` or `api.py` moves them at all, that patch is wrong.
