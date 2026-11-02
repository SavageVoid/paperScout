#!/usr/bin/env python3
"""Day 5 probe: does full-text search actually help on this corpus?

Read-only. For each query it runs two independent arms against the same tables —
a vector arm (Day 3 embeddings) and a keyword arm (Postgres full text over
paper_meta) — and reports each ranking plus how much they disagree.

Why measure first: fusion only earns its complexity if the arms disagree. If the
keyword arm only ever returns a subset of the vector arm, RRF adds latency and a
parameter for nothing. If the arms disagree a lot on named entities and rare
terms, that is exactly what fusion fixes.

Two keyword builders are compared: websearch_to_tsquery ANDs every term (strict,
returned 1 of 4,271 papers for a five-word query), while to_tsquery with |
ORs them and lets ts_rank_cd grade coverage.

No schema changes: the tsvector is computed inline. The real implementation will
add a stored generated column with a GIN index, which is much faster — this probe
is about quality, not speed.

    make -f day5.mk fts-probe
    python scripts/day5_fts_probe.py --explain        # print the SQL, touch nothing
"""

from __future__ import annotations

import argparse
import json
import sys
import time

DEFAULT_QUERIES = (
    "Yann LeCun",
    "sparse attention for long documents",
    "cosmic microwave background power spectra",
    "BERT pretraining masked language model",
    "diffusion model image generation",
)

# Same cosine expression the API uses: 1 - distance on unit vectors.
VECTOR_SQL = """
SELECT e.{idcol} AS arxiv_id, 1 - (e.embedding <=> %(vec)s::vector) AS score
  FROM {table} e
 WHERE e.embedding IS NOT NULL AND e.model_fp = %(fp)s
 ORDER BY e.embedding <=> %(vec)s::vector
 LIMIT %(limit)s
"""

# Deliberately inline to_tsvector: no schema change for a probe. The stored
# column lands in step 2, after this tells us whether it is worth having.
# Two query builders, compared side by side by the probe:
#   and  websearch_to_tsquery  -> every term must appear (strict, returns little)
#   or   to_tsquery with |     -> any term may appear, ts_rank_cd grades coverage
KEYWORD_SQL = """
SELECT m.arxiv_id,
       ts_rank_cd(
           to_tsvector('english', coalesce(m.title, '') || ' ' || coalesce(m.abstract, '')),
           {query_expr}
       ) AS score
  FROM {meta} m
 WHERE to_tsvector('english', coalesce(m.title, '') || ' ' || coalesce(m.abstract, ''))
       @@ {query_expr}
   AND (%(category)s::text IS NULL
        OR %(category)s::text = ANY(string_to_array(coalesce(m.categories, ''), ' ')))
 ORDER BY score DESC
 LIMIT %(limit)s
"""

QUERY_EXPR = {
    "and": "websearch_to_tsquery('english', %(q)s)",
    "or": "to_tsquery('english', %(tsq)s)",
}

# ground truth: does the corpus even mention the term?
ILIKE_SQL = """
SELECT count(*) FROM {meta}
 WHERE coalesce(title, '') || ' ' || coalesce(abstract, '') ILIKE %(pat)s
"""

TITLES_SQL = "SELECT arxiv_id, title FROM {meta} WHERE arxiv_id = ANY(%(ids)s)"


# Stopword-ish tokens we never want to OR in on their own
_MIN_TOKEN = 2


def tsquery_or(query: str) -> str:
    """Turn free text into an OR-ed tsquery: 'bert|pretrain|mask|...'."""
    import re

    tokens: list[str] = []
    for token in re.findall(r"[A-Za-z0-9]+", (query or "").lower()):
        if len(token) >= _MIN_TOKEN and token not in tokens:
            tokens.append(token)
    return " | ".join(tokens)


def vector_literal(values) -> str:
    return "[" + ",".join(f"{float(v):.6f}" for v in values) + "]"


def rrf(ranked_lists, k: int = 60) -> dict[str, float]:
    """Reciprocal rank fusion.

    score(doc) = sum over arms of 1 / (k + rank). Rank is 1-based. Documents
    missing from an arm simply do not contribute from it, which is what makes RRF
    robust to arms with different score scales.
    """
    fused: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, doc_id in enumerate(ranked, start=1):
            fused[doc_id] = fused.get(doc_id, 0.0) + 1.0 / (k + rank)
    return dict(sorted(fused.items(), key=lambda kv: -kv[1]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare vector vs keyword retrieval (read-only)")
    parser.add_argument("--queries", nargs="*", default=None, help="override the built-in queries")
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument("--fetch", type=int, default=20, help="rows pulled from each arm before fusing")
    parser.add_argument("--category", default=None)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--explain", action="store_true", help="print the SQL and exit")
    args = parser.parse_args(argv)

    render = {"table": "paper_embeddings", "meta": "paper_meta", "idcol": "paper_id"}
    vector_sql = VECTOR_SQL.format(**render)
    titles_sql = TITLES_SQL.format(**render)

    if args.explain:
        print("-- vector arm --")
        print(vector_sql)
        for mode, expr in QUERY_EXPR.items():
            print(f"-- keyword arm ({mode}) --")
            print(KEYWORD_SQL.format(**render, query_expr=expr))
        print("-- titles --")
        print(titles_sql)
        return 0

    import psycopg

    from docpulse.embed.cli import build_encoder
    from docpulse.embed.config import EmbeddingSettings

    settings = EmbeddingSettings.from_env()
    encoder = build_encoder(settings)
    queries = args.queries or list(DEFAULT_QUERIES)

    report: list[dict] = []
    with psycopg.connect(settings.dsn, connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT version()")
            print(f"postgres      {cur.fetchone()[0].split(',')[0]}")
        print(f"model         {settings.model_id} @ {settings.model_revision[:7]} "
              f"({getattr(encoder, 'dim', '?')}d)")
        print(f"queries       {len(queries)}   fetch {args.fetch} per arm   show {args.top}")
        print("")

        meta_sql = ILIKE_SQL.format(meta="paper_meta")
        for query in queries:
            vector = encoder.encode([query])[0]
            base = {"fp": encoder.fingerprint, "limit": args.fetch, "category": args.category}

            # ground truth first: does the corpus mention these tokens at all?
            ilike: dict[str, int] = {}
            for token in tsquery_or(query).split(" | ")[:3]:
                with conn.cursor() as cur:
                    cur.execute(meta_sql, {"pat": f"%{token}%"})
                    ilike[token] = int(cur.fetchone()[0])

            def timed(sql, params):
                started = time.perf_counter()
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                    rows = cur.fetchall()
                return rows, (time.perf_counter() - started) * 1000

            vec_rows, t_vec = timed(vector_sql, dict(base, vec=vector_literal(vector)))

            kw_rows: dict[str, list] = {}
            kw_ms: dict[str, float] = {}
            for mode in ("and", "or"):
                sql = KEYWORD_SQL.format(table="paper_embeddings", meta="paper_meta",
                                         idcol="paper_id", query_expr=QUERY_EXPR[mode])
                kw_rows[mode], kw_ms[mode] = timed(sql, dict(base, q=query, tsq=tsquery_or(query)))

            vec_ids = [r[0] for r in vec_rows]
            or_ids = [r[0] for r in kw_rows["or"]]
            and_ids = [r[0] for r in kw_rows["and"]]
            vec_score = dict(vec_rows)
            or_score = dict(kw_rows["or"])
            fused = rrf([vec_ids, or_ids])

            ids = list(dict.fromkeys(vec_ids + or_ids + and_ids + list(fused)))
            with conn.cursor() as cur:
                cur.execute(titles_sql.format(table="paper_embeddings", meta="paper_meta",
                                              idcol="paper_id"), {"ids": ids})
                titles = dict(cur.fetchall())

            overlap_or = len(set(vec_ids[:5]) & set(or_ids[:5]))
            overlap_and = len(set(vec_ids[:5]) & set(and_ids[:5]))
            report.append(
                {
                    "query": query,
                    "tsquery": tsquery_or(query),
                    "ilike_counts": ilike,
                    "vector_ms": round(t_vec, 1),
                    "keyword_ms": {k: round(v, 1) for k, v in kw_ms.items()},
                    "n_vector": len(vec_ids),
                    "n_keyword_and": len(and_ids),
                    "n_keyword_or": len(or_ids),
                    "overlap_at_5_and": overlap_and,
                    "overlap_at_5_or": overlap_or,
                    "vector": [{"id": i, "score": round(float(vec_score[i]), 4),
                                "title": titles.get(i)} for i in vec_ids[: args.top]],
                    "keyword_or": [{"id": i, "score": round(float(or_score[i]), 4),
                                    "title": titles.get(i)} for i in or_ids[: args.top]],
                    "fused": [{"id": i, "title": titles.get(i), "rrf": round(v, 6)}
                              for i, v in list(fused.items())[: args.top]],
                }
            )

            print(f"=== {query!r}")
            print(f"    tsquery: {tsquery_or(query)!r}")
            print("    corpus mentions (ILIKE): " +
                  "  ".join(f"{k}={v}" for k, v in ilike.items()))
            print(f"    vector {len(vec_ids):<3} ({t_vec:5.1f} ms)   "
                  f"kw-and {len(and_ids):<3} ({kw_ms['and']:5.1f} ms)   "
                  f"kw-or {len(or_ids):<3} ({kw_ms['or']:5.1f} ms)   "
                  f"overlap@5 vec~or: {overlap_or}")
            for i in vec_ids[: args.top]:
                print(f"    vec    {float(vec_score[i]):.3f}  {i:<12} {(titles.get(i) or '')[:64]}")
            if or_ids:
                for i in or_ids[: args.top]:
                    print(f"    kw-or  {float(or_score[i]):.4f} {i:<12} {(titles.get(i) or '')[:64]}")
            else:
                print("    kw-or  (nothing)")
            print("    --- rrf fusion (vector + keyword-or) ---")
            for rank, i in enumerate(list(fused)[: args.top], start=1):
                print(f"    #{rank}    {i:<12} {(titles.get(i) or '')[:64]}")
            print("")

    if args.json:
        print(json.dumps(report, indent=2))

    with_or = [r for r in report if r["n_keyword_or"] >= 5]
    thin = [r["query"] for r in report if r["n_keyword_or"] < 5]
    print(f"keyword-or coverage: {len(with_or)}/{len(report)} queries returned >= 5 rows")
    if thin:
        print(f"  thin arm for: {thin}")
        print("  a thin keyword arm makes fused overlap meaningless — widen the arm first")
    if len(with_or) >= 3:
        mean_or = sum(r["overlap_at_5_or"] for r in with_or) / len(with_or)
        print(f"  mean overlap@5 (vector vs keyword-or): {mean_or:.1f} of 5 over {len(with_or)} queries")
        if mean_or >= 4:
            print("  verdict: the OR arm mostly agrees with vectors — fusion adds little; consider")
            print("           shipping keyword as a mode for exact/name queries only.")
        elif mean_or <= 2:
            print("  verdict: the OR arm finds different papers — RRF fusion is justified (step 3).")
        else:
            print("  verdict: partial agreement — build hybrid behind a mode toggle and A/B it.")
    empty = [r["query"] for r in report if r["n_keyword_or"] == 0]
    if empty:
        print(f"  note: keyword-or still empty for {empty} — check the ILIKE counts above:")
        print("        zero ILIKE matches means the corpus genuinely lacks the term, not a bug.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
