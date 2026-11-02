"""Keyword retrieval over the stored weighted tsvector.

Two modes, decided per query, because the coverage measurements said the corpus is
small enough that exact matching usually has an answer:

  strict   every query term must appear (AND). On this corpus that returns 1-17
           rows for a specific query, which is the answer the user wanted, not a
           semantic neighbourhood of it.
  relaxed  only used when strict finds nothing: any term may appear (OR), ordered
           by how many of the query's terms each paper actually matches, then by
           ts_rank_cd. Coverage ordering is what fixes the failure the probe found
           -- ts_rank_cd has no IDF, so without it a paper matching only "model"
           (76% of the corpus) outranks one matching the rare terms.

Two rules that are load-bearing, both learned the hard way:

  * No to_tsvector in any WHERE clause: the match runs against paper_meta.fts, the
    stored column, so the GIN index can serve it.
  * No to_tsquery anywhere. English stemming is not idempotent -- "sparse" and
    "spars" are two different lexemes ("spars" and "spar") -- so feeding stems back
    through the dictionary searches for words no document contains. The tsquery is
    built here as a literal and cast, so the dictionary runs exactly once, when the
    fts column is written.
"""

from __future__ import annotations

import re

# {D, C, B, A} - title (A) counts more than abstract (B). Same constants the probes
# measured, so the behaviour is the one that was validated.
WEIGHTS = "'{0.05,0.2,0.3,1.0}'::float4[]"

MIN_TOKEN = 2
CANDIDATES = 200
SORTS = ("relevance", "updated")

# Day 4's category predicate, unchanged: paper_meta.categories is space separated.
CATEGORY_PREDICATE = (
    "AND (%(category)s::text IS NULL "
    "OR %(category)s::text = ANY(string_to_array(coalesce(m.categories, ''), ' ')))"
)

# The dictionary is applied here and never again: this returns the lexemes exactly
# as they are stored in paper_meta.fts.
STEMS_SQL = """
SELECT tsvector_to_array(to_tsvector('english'::regconfig, %(q)s)) AS stems
"""

ORDER = {
    "relevance": "score DESC",
    "updated": "m.updated DESC NULLS LAST, score DESC",
}


def tsquery(stems, operator: str) -> str:
    """Build a tsquery literal from already-stemmed lexemes.

    Quotes are escaped because the string is cast to tsquery, which parses it.
    """
    safe = [s.replace("'", "''") for s in stems]
    return f" {operator} ".join(f"'{s}'" for s in safe)


def _strict_sql(sort: str) -> str:
    return f"""
SELECT m.arxiv_id, m.title, ts_rank_cd({WEIGHTS}, m.fts, %(tsq)s::tsquery) AS score
  FROM paper_meta m
 WHERE m.fts @@ %(tsq)s::tsquery
   {CATEGORY_PREDICATE}
 ORDER BY {ORDER[sort]}
 LIMIT %(limit)s
"""


def _relaxed_sql(sort: str) -> str:
    order = ("coverage DESC, c.score DESC" if sort == "relevance"
             else "c.updated DESC NULLS LAST, c.score DESC")
    return f"""
WITH cand AS (
    SELECT m.arxiv_id, m.title, m.fts, m.updated,
           ts_rank_cd({WEIGHTS}, m.fts, %(tsq)s::tsquery) AS score
      FROM paper_meta m
     WHERE m.fts @@ %(tsq)s::tsquery
       {CATEGORY_PREDICATE}
     ORDER BY score DESC
     LIMIT %(cand)s
)
SELECT c.arxiv_id, c.title,
       (SELECT count(*) FROM (SELECT unnest(tsvector_to_array(c.fts))
                              INTERSECT
                              SELECT unnest(%(stems)s::text[])) AS shared) AS coverage,
       c.score
  FROM cand c
 ORDER BY {order}
 LIMIT %(limit)s
"""


def tokens(query: str) -> list[str]:
    """Cheap pre-check only; Postgres does the real stemming via STEMS_SQL."""
    return [t for t in re.findall(r"[A-Za-z][A-Za-z0-9]*", (query or "").lower())
            if len(t) >= MIN_TOKEN]


def _hit(row, total: int, mode: str) -> dict:
    if mode == "strict":
        arxiv_id, title, score = row
        coverage = total
    else:
        arxiv_id, title, coverage, score = row
    return {
        "arxiv_id": arxiv_id,
        "title": title,
        "score": round(float(score), 4),
        "coverage": int(coverage),
        "total": total,
        "mode": mode,
    }


def search_keyword(
    cur,
    query: str,
    limit: int = 10,
    category: str | None = None,
    sort: str = "relevance",
    cand: int = CANDIDATES,
) -> dict:
    """Run the strict-then-relaxed keyword search. Returns a diagnostic dict.

    {"mode": "strict"|"relaxed"|"empty", "stems": [...], "rows": [...]}

    The caller gets the mode so the API can tell the user which one answered:
    a strict hit is an exact match and should be presented as such.
    """
    if sort not in ORDER:
        raise ValueError(f"sort must be one of {SORTS}, got {sort!r}")
    if not tokens(query):
        return {"mode": "empty", "stems": [], "rows": []}

    cur.execute(STEMS_SQL, {"q": query})
    row = cur.fetchone()
    stems = [str(s) for s in (row[0] if row and row[0] else [])]
    if not stems:
        # every term was a stopword, so no tsquery can be built from it
        return {"mode": "empty", "stems": [], "rows": []}

    params = {"limit": limit, "category": category, "stems": stems}
    cur.execute(_strict_sql(sort), dict(params, tsq=tsquery(stems, "&")))
    rows = [_hit(r, len(stems), "strict") for r in cur.fetchall()]
    if rows:
        return {"mode": "strict", "stems": stems, "rows": rows}

    cur.execute(_relaxed_sql(sort), dict(params, tsq=tsquery(stems, "|"), cand=cand))
    rows = [_hit(r, len(stems), "relaxed") for r in cur.fetchall()]
    return {"mode": "relaxed" if rows else "empty", "stems": stems, "rows": rows}
