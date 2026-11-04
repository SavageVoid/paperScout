"""Day 6: author search, exact first and partial only as a fallback.

Same shape as `keyword.py`, deliberately: the API should be able to treat a mode
as "something that returns a mode, a diagnostic and a list of rows".

Three indexes exist and the planner picked all three on its own (Day 6 stage 3):

    authors      text[]    display form, original order, 'Yann LeCun'
    authors_ci   text[]    lowercased, for case-insensitive exact match
    authors_tsv  tsvector  'simple' config, for last-name and partial match

The arms, in order:

  exact    authors_ci @> ARRAY[key]      'Yann LeCun' -> the LeCun paper
  partial  authors_tsv @@ plainto_tsquery('simple', key)   'lecun' -> same paper

Partial is a fallback, not a supplement: if exact answered, nothing is padded in
from the partial tail. Mixing "this person is an author" with "this string appears
among some author's names" under one list is unreadable, and it is the same rule
Day 5 settled for strict/relaxed keyword search.

There is no author disambiguation and there cannot be: arXiv ships no author
identifiers. "Wei Wang" is many people, which is why the response says how many
papers matched rather than pretending the hits are one person's.
"""

from __future__ import annotations

import re

MIN_KEY = 2

# Day 4's category predicate, and the date window db.search uses, unchanged.
FILTERS = """
   AND (%(category)s::text IS NULL
        OR %(category)s::text = ANY(string_to_array(coalesce(m.categories, ''), ' ')))
   AND (%(since)s::timestamptz IS NULL OR m.published >= %(since)s::timestamptz)
   AND (%(until)s::timestamptz IS NULL OR m.published <  %(until)s::timestamptz)
"""

# Exact containment, case-insensitive. Every hit matches equally well, so there is
# no relevance score to order by; newest first is the honest order, and it makes
# sort=updated a no-op rather than a lie.
EXACT_SQL = f"""
SELECT m.arxiv_id, m.title, m.authors, 1.0::float8 AS score
  FROM paper_meta m
 WHERE m.authors_ci @> ARRAY[%(key)s]::text[]
   {FILTERS}
 ORDER BY m.updated DESC NULLS LAST, m.arxiv_id
 LIMIT %(limit)s
"""

# 'simple', never 'english': stemming would turn Harris into harri.
PARTIAL_SQL = f"""
SELECT m.arxiv_id, m.title, m.authors,
       ts_rank(m.authors_tsv, plainto_tsquery('simple', %(key)s)) AS score
  FROM paper_meta m
 WHERE m.authors_tsv @@ plainto_tsquery('simple', %(key)s)
   {FILTERS}
 ORDER BY score DESC, m.updated DESC NULLS LAST, m.arxiv_id
 LIMIT %(limit)s
"""

COUNT_SQL = """
SELECT count(*) FROM paper_meta m WHERE m.authors_ci @> ARRAY[%(key)s]::text[]
"""


def normalise(author: str | None) -> str:
    """Collapse whitespace and lowercase, matching Postgres lower() for ASCII.

    Deliberately the same transformation that built authors_ci. If these two ever
    disagree, exact matching fails silently for names with odd spacing.
    """
    return " ".join((author or "").split()).lower()


def tokens(author: str | None) -> list[str]:
    """Cheap pre-check only; Postgres builds the real tsquery from the key."""
    return [t for t in re.findall(r"[^\W\d_][\w'-]*", (author or "").lower())
            if len(t) >= MIN_KEY]


def _row(row) -> dict:
    arxiv_id, title, authors, score = row
    return {
        "arxiv_id": arxiv_id,
        "title": title,
        "authors": list(authors or []),
        "score": round(float(score), 4),
    }


def search_author(
    cur,
    author: str,
    limit: int = 10,
    category: str | None = None,
    since: str | None = None,
    until: str | None = None,
    count_saturated: bool = True,
) -> dict:
    """Exact author match, falling back to a partial name match.

    {"mode": "exact"|"partial"|"empty", "author": str, "key": str,
     "rows": [...], "total_matches": int | None}

    total_matches is only filled when the answer hit the limit, which is the one
    case where "40 rows" is ambiguous between "40 papers" and "the first 40 of
    400" -- the same saturation problem Day 5 hit on 7 of 10 keyword queries.
    """
    key = normalise(author)
    if not tokens(author) or not key:
        return {"mode": "empty", "author": "", "key": key, "rows": [],
                "total_matches": None}

    params = {"key": key, "limit": limit, "category": category,
              "since": since, "until": until}

    cur.execute(EXACT_SQL, params)
    rows = [_row(r) for r in cur.fetchall()]
    mode = "exact"
    if not rows:
        cur.execute(PARTIAL_SQL, params)
        rows = [_row(r) for r in cur.fetchall()]
        mode = "partial"

    total = None
    if rows and count_saturated and len(rows) >= limit:
        cur.execute(COUNT_SQL, {"key": key})
        found = cur.fetchone()
        total = int(found[0]) if found else None

    return {
        "mode": mode if rows else "empty",
        "author": " ".join((author or "").split()),
        "key": key,
        "rows": rows,
        "total_matches": total,
    }
