# Day 6 — author search

Day 5 ended with two queries that returned nothing:

```
"Yann LeCun"       0 rows
"Geoffrey Hinton"  0 rows
```

They now return their papers. Both were read as evidence that `paper_meta` had no
authors column, and that was right — but the first thing Day 6 did was check that
the *people* were in the corpus at all. They are, one paper each. If they had not
been, author search could have been correct on the first attempt and still
returned zero, and a day would have gone into debugging a feature that worked.

## Where the names came from

The harvest is in the object store (SeaweedFS, bucket `docpulse`, key prefix
`bronze/arxiv/ingest_date=YYYY-MM-DD/page-NNNNN.xml`), not on disk and not in the
database. 69 objects, **13,000 records**, **4,271 papers** — each paper appears
about **3.04 times** because ingest windows overlap and papers get revised.

**The newest version of each paper wins.** Keeping whichever record arrived first
would have backfilled a stale author list whenever a revision changed it, and the
dry run measured exactly how often that happens:

```
gained authors   6  ['2610.37369', '2610.37325', '2610.36527', ...]
lost authors     1  ['2610.33745']
```

Seven papers would have been wrong. Small, silent, and invisible to any test that
only counts rows.

## What the names look like

Measured before any fold rule was chosen, because guessing at name handling is how
you get a search that works for `Smith` and not for `Hervé`:

| shape | count | examples |
|---|---|---|
| non-ASCII | 542 | `Nuno Gonçalves`, `Baptiste Rozière` |
| braces around an accent | 8 | `Herv{é} Panetto`, `Łukasz Smoli{ń}ski` |
| particles (van/de/le…) | 245 | `Yves-Alexandre de Montjoye`, `Boris van Breugel` |
| single token | 28 | `Plato`, `Yeshani` |
| names differing only by case | 5 pairs | `Zhichao Hu` / `Zhichao hu` |
| repeated author within a paper | 2 papers | genuine duplicates, not an error |

The eight braced names are a rendering artefact: left alone, `Herv{é} Panetto`
can never match someone typing `Hervé`. They are folded at load time. Accents are
otherwise **preserved, not stripped** — folding them needs the `unaccent`
extension and is a separate decision with its own trade-off.

23,445 name entries, **19,854 distinct**, mean 5.49 authors per paper, longest
list **559**.

## The three columns

```sql
authors      text[]     display form, original order      'Yann LeCun'
authors_ci   text[]     lowercased, exact match           'yann lecun'
authors_tsv  tsvector   'simple' config, partial match    yann, lecun
```

Each with a GIN index. The planner chooses all three unaided, at 4,271 rows:

```
authors_ci @> ARRAY['yann lecun']       -> Bitmap Index Scan on paper_meta_authors_ci_idx
authors_tsv @@ plainto_tsquery(...)     -> Bitmap Index Scan on paper_meta_authors_tsv_idx
authors @> ARRAY['Yann LeCun']          -> Bitmap Index Scan on paper_meta_authors_idx
```

`authors_tsv` is a **plain column, not `GENERATED ALWAYS`**: `array_to_string()` is
`STABLE`, not `IMMUTABLE`, and PostgreSQL refuses stable expressions in generation
expressions. The backfill fills both in one statement, and the loader must keep
them in step — a row written without `authors_tsv` is findable by exact name and
not by surname. The load script checks for exactly that.

`authors_ci` exists because `@>` is case-sensitive and users type lowercase.
Phrase-matching `authors_tsv` instead would occasionally match one author's first
name against another author's surname in the same paper.

## The arms

```
exact    authors_ci @> ARRAY[key]                       'Yann LeCun'
partial  authors_tsv @@ plainto_tsquery('simple', key)  'lecun'
```

Partial is a **fallback, never a supplement**: if exact answered, nothing is
padded in from the partial tail. Same rule Day 5 settled for strict/relaxed, for
the same reason — a list that mixes "this person is an author" with "this string
appears among some author's names" is unreadable.

Exact results are ordered **newest first**, because every exact hit matches equally
well and there is no relevance to rank by. `sort=updated` is therefore a no-op in
author mode rather than a lie.

## API

```
GET /search?author=Yann+LeCun
{"mode": "author", "n_returned": 1,
 "author": {"mode": "exact", "key": "yann lecun", "total_matches": null},
 "results": [{"arxiv_id": "2610.39302", "authors": ["Randall Balestriero", "Yann LeCun"], …}]}
```

- `q` is now optional; **422 when neither `q` nor `author` is given**, and still
  422 for a whitespace-only `q`.
- `author=` **wins over `q=`** when both are present: one unambiguous claim per
  request. `mode` in the response says which arm answered, so this is never silent.
- `min_score` is ignored here — a cosine floor means nothing to a name match.
- `total_matches` is filled **only when the answer hits the limit**, the one case
  where "10 rows" is ambiguous between ten papers and the first ten of many.
- `authors` is attached to every result row, in both other modes too.

## Decisions and the evidence behind them

- **Authors do not go into the document `fts`.** Doing so would change the stored
  column, the index, `n_exact` and every number in the Day-5 probe. `q=LeCun` in
  `mode=keyword` still returns nothing, by design.
- **`simple`, never `english`.** English stemming turns `Harris` into `harri` and
  mangles hyphenated and accented names into lexemes no document contains.
- **No disambiguation, and none is possible.** arXiv ships no author identifiers.
  `Wei Wang` returns ten papers by whoever shares that name; the response says ten
  rather than implying one person. Any claim beyond that would be invention.
- **Revision-aware backfill**, measured above at 6 gained / 1 lost.

## Known gaps

1. **Accents are not folded.** `Herve Panetto` will not find `Hervé Panetto`. Fix
   needs `unaccent` (available in this Postgres image, unverified for the
   extension) plus a decision about whether to fold display names too.
2. **Homonyms are unresolvable**, as above. `total_matches` is the honest signal.
3. **No first-author ranking.** `authors` preserves order so it is recoverable;
   `authors_ci` does not. Nobody has needed it yet.
4. **A 559-author paper** is stored in full and will render badly unless the
   response trims it.
5. **`author=` ignores `q=`.** Deliberate, documented, reversible in the branch.

## Operational facts learned the hard way

- The papers live in the database **`docpulse_embed`**, not `docpulse`. `psql`
  against the wrong one reports `paper_meta does not exist` while the API answers
  queries happily.
- `./src` is bind-mounted **read-only**; `docker cp` into it fails. Edit the host
  file and it is already visible in the container. New files also need
  `chcon -t container_file_t`, or the container gets `EACCES` despite mode 644.
- Postgres was **down for 11 hours** while `searchapi` stayed `Up` and `/health`
  returned 200. Container status says nothing about the database behind it.
