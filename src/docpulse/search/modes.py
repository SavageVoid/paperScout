"""Day 5: the policy behind /search?mode=..., kept out of the HTTP layer.

The endpoint has to settle three things before it returns a row: which arm ran,
what order the rows come back in, and whether a score floor was applied. All three
are decisions over a list of dicts, so they live here, where they can be tested
without FastAPI, a database or an encoder.

Both defaults are measured, not chosen:

  min_score is opt-in and defaults to off. On the probe corpus the best
  absent-topic top1 was 0.276 (author queries, no author column exists) and the
  worst present-topic top1 was 0.496 -- a gap of two samples. The one correct CMB
  hit scored 0.515, barely above the query that has no right answer. A floor that
  silently drops results on that evidence would be guesswork.

  mode=keyword reports whether the strict AND arm answered or the OR fallback did,
  because the probe showed 7 of 10 strict answers arriving exactly at the limit:
  AND proves the words are present, not that the paper is about them.
"""

from __future__ import annotations

from typing import Any

MODES = ("vector", "keyword")
SORTS = ("relevance", "updated")

# sort=updated cannot mean "newest in the corpus" -- that would ignore the query
# entirely. It means "newest among the most relevant", so the vector arm fetches a
# wider pool by similarity and only then re-orders it.
POOL_FACTOR = 10
POOL_MIN = 100
POOL_MAX = 1000


def candidate_pool(limit: int) -> int:
    """How many similar rows to pull when the caller wants them re-ordered by date."""
    return max(POOL_MIN, min(POOL_MAX, int(limit) * POOL_FACTOR))


def order_by_updated(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Newest first; rows with no date go last; ties keep their similarity order."""
    return sorted(
        rows,
        key=lambda row: (row.get("updated") is not None, row.get("updated")),
        reverse=True,
    )[: int(limit)]


def apply_min_score(
    rows: list[dict[str, Any]], min_score: float | None
) -> list[dict[str, Any]]:
    """Drop rows below the floor. None means no floor, which is the default."""
    if min_score is None:
        return rows
    return [row for row in rows if float(row["score"]) >= float(min_score)]


def merge_keyword_rows(
    hits: list[dict[str, Any]], meta_by_id: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Attach metadata to keyword hits, preserving the rank the engine chose.

    A hit with no metadata row was filtered out by the caller's date range, so it
    is dropped rather than returned without a title.
    """
    rows = []
    for hit in hits:
        meta = meta_by_id.get(hit["arxiv_id"])
        if meta is None:
            continue
        row = dict(meta)
        row["score"] = float(hit["score"])
        rows.append(row)
    return rows


def keyword_report(
    found: dict[str, Any], rows: list[dict[str, Any]]
) -> dict[str, Any]:
    """What the caller needs in order to describe these results honestly."""
    rows_in = found.get("rows") or []
    return {
        "mode": found.get("mode", "empty"),
        "stems": list(found.get("stems") or []),
        "n_exact": len(rows_in) if found.get("mode") == "strict" else 0,
        "n_returned": len(rows),
        "coverage": [
            {"arxiv_id": hit["arxiv_id"], "matched": hit["coverage"], "of": hit["total"]}
            for hit in rows_in
        ],
    }
