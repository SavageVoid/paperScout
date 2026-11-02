#!/usr/bin/env python3
"""Day 5 step 2: is the keyword arm's *ranking* good enough to fuse?

Two earlier measurements were wrong, and both were wrong in the same direction —
they blamed the data for a fault in the tool:

  1. The AND arm (websearch_to_tsquery) returned 0-2 rows and looked like proof
     that the corpus has no relevant papers. It was proof that AND is too strict.
  2. After switching to OR, the arms overlapped at 1.0 of 5 and the probe printed
     "RRF fusion is justified". That verdict over-claims. ts_rank_cd has no IDF,
     so on this corpus ("model" appears in 77% of papers) it put
     "The Value of System 1 Models in Medicine" at rank 4 for the query
     "diffusion model image generation". Two arms "disagreeing" is only useful if
     the second arm is ordering papers sensibly in the first place.

So this probe asks the three questions that actually decide what Day 5 ships:

  1. Corpus truth  - document frequency per query token, plus how many papers
                     match 1, 2, ... n of them. Separates "absent from the
                     corpus" (a metadata problem) from "the arm cannot find it"
                     (a ranking problem).
  2. Ranking       - plain ts_rank_cd vs title-weighted vs title-weighted with
                     high-df tokens dropped. Scored by anchor hit rate: do the
                     top 5 even contain the query's rarest token?
  3. Vector floor  - best and weakest vector scores per query, so min_score is
                     picked from data instead of taste.

Read-only; the inline tsvector is deliberately slow. Speed is step 3 (stored
column + GIN index). Ranking quality is this probe's problem.

    make -f day5.mk rank-probe
    python scripts/day5_rank_probe.py --explain
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time

DEFAULT_QUERIES = (
    # topics that must work
    "sparse attention for long documents",
    "diffusion model image generation",
    "BERT pretraining masked language model",
    "retrieval augmented generation",
    "LoRA fine-tuning",
    "quantization of neural networks",
    "reinforcement learning from human feedback",
    # a title the corpus may literally contain
    "attention is all you need",
    # people: abstracts do not usually name authors
    "Yann LeCun",
    "Geoffrey Hinton",
    # out of domain for an ML corpus, so a well-behaved system says "nothing"
    "cosmic microwave background power spectra",
    "protein structure prediction",
)

# Postgres' english config drops these anyway; stripping them here keeps the
# document-frequency table readable ("for" is in 4,176 of 4,271 papers).
STOPWORDS = frozenset("""
a about above after again against all also am an and any are as at be because been before
being below between both but by can could did do does doing down during each few for from
further had has have having he her here hers herself him himself his how i if in into is it
its itself just me more most my myself no nor not now of off on once only or other our ours
ourselves out over own same she should so some such than that the their theirs them themselves
then there these they this those through to too under until up very via was we were what when
where which while who whom why will with would you your yours
""".split())

MIN_TOKEN = 2
MAX_DF_RATIO = 0.10  # a token in >10% of the corpus carries no discriminating power
KEEP_MIN = 2  # never prune below this many tokens, even if all are common
ANCHOR_COUNT = 2  # the rarest tokens are the ones a relevant hit must contain

# Same cosine expression the API uses: 1 - distance on unit vectors.
VECTOR_SQL = """
SELECT e.paper_id AS arxiv_id, 1 - (e.embedding <=> %(vec)s::vector) AS score
  FROM paper_embeddings e
 WHERE e.embedding IS NOT NULL AND e.model_fp = %(fp)s
 ORDER BY e.embedding <=> %(vec)s::vector
 LIMIT %(limit)s
"""

TSV = "to_tsvector('english', coalesce(m.title, '') || ' ' || coalesce(m.abstract, ''))"
# Title in class A, abstract in class B: a query term found in the title is worth
# more than the same term buried in the abstract.
TSV_W = ("setweight(to_tsvector('english', coalesce(m.title, '')), 'A') || "
         "setweight(to_tsvector('english', coalesce(m.abstract, '')), 'B')")
# {D, C, B, A}: abstract 0.3, title 1.0. A constant, never formatted.
WEIGHTS = "'{0.05,0.2,0.3,1.0}'::float4[]"
Q = "to_tsquery('english', %(tsq)s)"

TITLES_SQL = "SELECT arxiv_id, title FROM paper_meta WHERE arxiv_id = ANY(%(ids)s::text[])"


# ---------------------------------------------------------------- pure helpers


def tokenize(query: str) -> list[str]:
    """Query text -> searchable tokens, in query order, deduped, stopwords gone."""
    tokens: list[str] = []
    for token in re.findall(r"[A-Za-z][A-Za-z0-9]*", (query or "").lower()):
        if len(token) >= MIN_TOKEN and token not in STOPWORDS and token not in tokens:
            tokens.append(token)
    return tokens


def or_query(tokens: list[str]) -> str:
    return " | ".join(tokens)


def pick_anchors(tokens: list[str], df: dict[str, int], n: int = ANCHOR_COUNT) -> list[str]:
    """The rarest tokens that the corpus actually contains.

    Tokens with df 0 are excluded: they can never appear in a hit, so counting
    them would just depress every arm equally and tell us nothing.
    """
    present = [t for t in tokens if df.get(t, 0) > 0]
    return sorted(present, key=lambda t: (df[t], tokens.index(t)))[:n]


def prune(
    tokens: list[str],
    df: dict[str, int],
    total: int,
    max_ratio: float = MAX_DF_RATIO,
    keep_min: int = KEEP_MIN,
) -> list[str]:
    """Drop tokens too common to discriminate, but never empty the query.

    A token with df 0 is kept on purpose: absent terms are the most
    discriminative ones available, they simply match nothing.
    """
    kept = [t for t in tokens if df.get(t, 0) <= max_ratio * total]
    if len(kept) < keep_min:
        for token in sorted(tokens, key=lambda t: (df.get(t, 0), tokens.index(t))):
            if token not in kept:
                kept.append(token)
            if len(kept) >= keep_min:
                break
    return [t for t in tokens if t in kept]


def coverage(hist: dict[int, int], n_tokens: int) -> dict[str, int]:
    """Turn the match-count histogram into the numbers worth printing."""
    return {
        "full": hist.get(n_tokens, 0),
        "near": hist.get(max(n_tokens - 1, 1), 0),
        "any": sum(hist.values()),
    }


def hit_rate(ids: list[str], hits: dict[str, int], k: int = 5) -> int:
    """How many of the top k results contain at least one anchor token."""
    return sum(1 for i in ids[:k] if hits.get(i, 0) > 0)


# ------------------------------------------------------------- SQL renderers


def df_sql() -> str:
    """One scan, exact lexeme document frequency for every token at once."""
    return f"""
SELECT t.tok, count(*) AS df
  FROM paper_meta m, unnest(%(toks)s::text[]) AS t(tok)
 WHERE {TSV} @@ to_tsquery('english', t.tok)
 GROUP BY t.tok
"""


def coverage_sql() -> str:
    """One scan, histogram of 'how many of the query's tokens does each paper match'."""
    return f"""
WITH hit AS (
    SELECT m.arxiv_id, t.tok
      FROM paper_meta m, unnest(%(toks)s::text[]) AS t(tok)
     WHERE {TSV} @@ to_tsquery('english', t.tok)
), per AS (
    SELECT arxiv_id, count(*) AS n FROM hit GROUP BY arxiv_id
)
SELECT n, count(*) FROM per GROUP BY n ORDER BY n DESC
"""


def ranked_sql(tsv: str, rank: str) -> str:
    return f"""
SELECT m.arxiv_id, {rank} AS score
  FROM paper_meta m
 WHERE {tsv} @@ {Q}
 ORDER BY score DESC
 LIMIT %(limit)s
"""


def anchor_sql() -> str:
    return f"""
SELECT m.arxiv_id, count(*) AS hits
  FROM paper_meta m, unnest(%(anchors)s::text[]) AS t(tok)
 WHERE m.arxiv_id = ANY(%(ids)s::text[])
   AND {TSV} @@ to_tsquery('english', t.tok)
 GROUP BY m.arxiv_id
"""


def variants() -> dict[str, tuple[str, str]]:
    """name -> (tsv expression, rank expression)."""
    return {
        "plain": (TSV, f"ts_rank_cd({TSV}, {Q})"),
        "weighted": (TSV_W, f"ts_rank_cd({WEIGHTS}, {TSV_W}, {Q})"),
    }


def vector_literal(values) -> str:
    return "[" + ",".join(f"{float(v):.6f}" for v in values) + "]"


SCHEMA_COLUMNS_SQL = """
SELECT table_name, column_name, data_type, is_nullable
  FROM information_schema.columns
 WHERE table_schema = 'public' AND table_name IN ('paper_meta', 'paper_embeddings')
 ORDER BY table_name, ordinal_position
"""

SCHEMA_INDEXES_SQL = """
SELECT tablename, indexname, indexdef
  FROM pg_indexes
 WHERE schemaname = 'public' AND tablename IN ('paper_meta', 'paper_embeddings')
 ORDER BY tablename, indexname
"""


def print_explain() -> None:
    print("-- schema --")
    print(SCHEMA_COLUMNS_SQL.strip())
    print(SCHEMA_INDEXES_SQL.strip())
    print("\n-- document frequency / coverage --")
    print(df_sql().strip() + "\n")
    print(coverage_sql().strip())
    print("\n-- ranked arms --")
    for name, (tsv, rank) in variants().items():
        print(f"-- {name} --")
        print(ranked_sql(tsv, rank).strip())
    print("\n-- anchor check --")
    print(anchor_sql().strip())
    print("\n-- vector arm --")
    print(VECTOR_SQL.strip())
    print("\n-- titles --")
    print(TITLES_SQL)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Is the keyword arm's ranking good enough to fuse? (read-only)"
    )
    parser.add_argument("--queries", nargs="*", default=None, help="override the built-in queries")
    parser.add_argument("--top", type=int, default=3, help="titles to print per arm")
    parser.add_argument("--fetch", type=int, default=20, help="rows pulled from each arm")
    parser.add_argument("--max-df", type=float, default=MAX_DF_RATIO,
                        help="drop tokens present in more than this fraction of the corpus")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--explain", action="store_true", help="print the SQL and exit")
    args = parser.parse_args(argv)

    if args.explain:
        print_explain()
        return 0

    import psycopg

    from docpulse.embed.cli import build_encoder
    from docpulse.embed.config import EmbeddingSettings

    settings = EmbeddingSettings.from_env()
    encoder = build_encoder(settings)
    queries = args.queries or list(DEFAULT_QUERIES)

    def ms(started: float) -> float:
        return (time.perf_counter() - started) * 1000

    report: list[dict] = []
    with psycopg.connect(settings.dsn, connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT version()")
            print(f"postgres   {cur.fetchone()[0].split(',')[0]}")
            cur.execute("SELECT count(*) FROM paper_meta")
            total = int(cur.fetchone()[0])
            print(f"corpus     {total} papers in paper_meta")
            print(f"model      {settings.model_id} @ {settings.model_revision[:7]}")
            print(f"max_df     {args.max_df:.0%} of the corpus  ({int(args.max_df * total)} papers)")
            print("")
            print("-- schema (paper_meta, paper_embeddings) --")
            cur.execute(SCHEMA_COLUMNS_SQL)
            for table, column, dtype, nullable in cur.fetchall():
                name = f"{table}.{column}"
                print(f"   {name:<34} {dtype.strip():<20} {'null' if nullable == 'YES' else ''}")
            cur.execute(SCHEMA_INDEXES_SQL)
            indexes = cur.fetchall()
            if indexes:
                print("-- indexes --")
                for table, name, definition in indexes:
                    label = f"{table}.{name}"
                    print(f"   {label:<34} {definition.split(' USING ')[-1][:70]}")
            else:
                print("-- indexes -- none")
            print("")

        for query in queries:
            tokens = tokenize(query)
            if not tokens:
                print(f"=== {query!r}\n    no searchable tokens\n")
                continue

            # 1. corpus truth
            df = {t: 0 for t in tokens}
            started = time.perf_counter()
            with conn.cursor() as cur:
                cur.execute(df_sql(), {"toks": tokens})
                for tok, n in cur.fetchall():
                    df[tok] = int(n)
            df_ms = ms(started)
            with conn.cursor() as cur:
                cur.execute(coverage_sql(), {"toks": tokens})
                hist = {int(n): int(c) for n, c in cur.fetchall()}
            cov = coverage(hist, len(tokens))

            anchors = pick_anchors(tokens, df)
            kept = prune(tokens, df, total, args.max_df)
            dropped = [t for t in tokens if t not in kept]
            anchored = bool(anchors)

            # 2. ranked arms
            arms: dict[str, dict] = {}
            arm_sql = variants()
            plans = [("plain", arm_sql["plain"], tokens)]
            plans.append(("weighted", arm_sql["weighted"], tokens))
            plans.append(("pruned", arm_sql["weighted"], kept))
            for name, (tsv, rank), toks in plans:
                started = time.perf_counter()
                with conn.cursor() as cur:
                    cur.execute(ranked_sql(tsv, rank), {"tsq": or_query(toks), "limit": args.fetch})
                    rows = cur.fetchall()
                arms[name] = {
                    "ids": [r[0] for r in rows],
                    "scores": [float(r[1]) for r in rows],
                    "ms": ms(started),
                    "tsq": or_query(toks),
                }

            # 3. vector arm
            vector = encoder.encode([query])[0]
            started = time.perf_counter()
            with conn.cursor() as cur:
                cur.execute(
                    VECTOR_SQL,
                    {"vec": vector_literal(vector), "fp": encoder.fingerprint, "limit": args.fetch},
                )
                rows = cur.fetchall()
            arms["vector"] = {
                "ids": [r[0] for r in rows],
                "scores": [float(r[1]) for r in rows],
                "ms": ms(started),
                "tsq": None,
            }

            # anchor hit rate, one cheap query over at most 4 x 5 ids
            hits: dict[str, int] = {}
            if anchored:
                ids = list({i for arm in arms.values() for i in arm["ids"][:5]})
                with conn.cursor() as cur:
                    cur.execute(anchor_sql(), {"anchors": anchors, "ids": ids})
                    hits = {i: int(n) for i, n in cur.fetchall()}
            for _name, arm in arms.items():
                arm["anchor_at_5"] = hit_rate(arm["ids"], hits) if anchored else None

            titles: dict[str, str] = {}
            shown = list({i for name in ("vector", "pruned") for i in arms[name]["ids"][: args.top]})
            if shown:
                with conn.cursor() as cur:
                    cur.execute(TITLES_SQL, {"ids": shown})
                    titles = dict(cur.fetchall())

            # ---- print
            print(f"=== {query!r}")
            print("    df       " + "  ".join(
                f"{t} {df[t]} ({df[t] / total:.1%})" for t in tokens))
            print(f"    coverage papers matching all {len(tokens)}: {cov['full']}   "
                  f"{len(tokens) - 1}: {cov['near']}   any: {cov['any']}   ({df_ms:.0f} ms)")
            if anchored:
                print("    anchors  " + ", ".join(f"{a}({df[a]})" for a in anchors))
            else:
                print("    anchors  none - every query token is absent from the corpus")
            if dropped:
                print("    pruned   dropped " + ", ".join(
                    f"{t} ({df[t] / total:.0%})" for t in dropped)
                    + f"   -> {' | '.join(kept)}")
            else:
                print("    pruned   nothing dropped - same query as weighted")
            header = f"    {'arm':<9}{'n':>4}{'anchor@5':>10}{'top1':>8}{'top5':>8}{'ms':>9}"
            print(header)
            for name in ("vector", "plain", "weighted", "pruned"):
                arm = arms[name]
                scores = arm["scores"]
                rate = "-" if arm["anchor_at_5"] is None else f"{arm['anchor_at_5']}/5"
                print(f"    {name:<9}{len(arm['ids']):>4}{rate:>10}"
                      f"{(scores[0] if scores else 0.0):>8.3f}"
                      f"{(scores[4] if len(scores) > 4 else 0.0):>8.3f}{arm['ms']:>9.0f}")
            for name in ("vector", "pruned"):
                for rank, arxiv_id in enumerate(arms[name]["ids"][: args.top], start=1):
                    title = (titles.get(arxiv_id) or "")[:62]
                    print(f"    {name:<7}#{rank} {arxiv_id:<12} {title}")
            print("")

            report.append({
                "query": query,
                "tokens": tokens,
                "df": df,
                "df_ratio": {t: round(df[t] / total, 4) for t in tokens},
                "coverage": cov,
                "anchors": anchors,
                "kept": kept,
                "dropped": dropped,
                "arms": {
                    name: {
                        "n": len(arm["ids"]),
                        "anchor_at_5": arm["anchor_at_5"],
                        "top1": round(arm["scores"][0], 4) if arm["scores"] else None,
                        "top5": round(arm["scores"][4], 4) if len(arm["scores"]) > 4 else None,
                        "ms": round(arm["ms"], 1),
                        "top": [{"id": i, "title": titles.get(i)} for i in arm["ids"][: args.top]],
                    }
                    for name, arm in arms.items()
                },
            })

    # ---- summary
    judged = [r for r in report if r["anchors"]]
    absent = [r["query"] for r in report if r["tokens"] and not r["anchors"]]
    print(f"judgeable queries: {len(judged)}/{len(report)} "
          f"(anchor tokens must exist in the corpus to score anything)")
    if judged:
        print(f"  {'arm':<9}{'mean anchor@5':>15}")
        means: dict[str, float] = {}
        for name in ("vector", "plain", "weighted", "pruned"):
            mean = sum(r["arms"][name]["anchor_at_5"] for r in judged) / len(judged)
            means[name] = mean
            print(f"  {name:<9}{mean:>15.1f}")
        gain = means["pruned"] - means["plain"]
        print("")
        if gain >= 0.4:
            print(f"  verdict: title weighting + df pruning gains {gain:+.1f} anchor@5 over plain")
            print("           ts_rank_cd. Build the stored weighted tsvector + GIN (step 3),")
            print("           then fuse - RRF is now worth its parameter.")
        elif gain >= 0.15:
            print(f"  verdict: ranking fix gains {gain:+.1f} anchor@5 - real but modest. Ship")
            print("           keyword as an opt-in mode, keep hybrid behind a flag, A/B later.")
        else:
            print(f"  verdict: ranking fix gains only {gain:+.1f} anchor@5. Postgres relevance")
            print("           ranking is not the right tool here; keyword earns a place only as")
            print("           an exact title/term filter, not as a fused arm.")
        if means["vector"] > means["pruned"]:
            print(f"  note: vectors still win on anchor@5 ({means['vector']:.1f} vs "
                  f"{means['pruned']:.1f}) - do not build fusion to 'rescue' vectors.")
    if absent:
        print(f"  absent terms (df 0 for every token): {absent}")
        print("    title+abstract cannot answer these. If the schema dump above shows no")
        print("    authors column, author search is a metadata gap, not a ranking gap.")
    ranked_by_top1 = sorted(
        (r for r in report if r["arms"]["vector"]["top1"] is not None),
        key=lambda r: r["arms"]["vector"]["top1"],
    )
    print("")
    print("vector score profile (floor calibration for min_score):")
    for r in ranked_by_top1:
        print(f"  {r['arms']['vector']['top1']:.3f}  top1   "
              f"{(r['arms']['vector']['top5'] or 0.0):.3f}  top5   {r['query']}")
    junk = [r["arms"]["vector"]["top1"] for r in ranked_by_top1 if not r["anchors"]]
    good = [r["arms"]["vector"]["top1"] for r in ranked_by_top1 if r["anchors"]]
    if junk and good:
        print(f"  best 'nothing relevant exists' top1: {max(junk):.3f}   "
              f"worst 'something exists' top1: {min(good):.3f}")
        if min(good) > max(junk):
            print(f"  -> a floor between {max(junk):.2f} and {min(good):.2f} separates them "
                  f"on this sample")
        else:
            print("  -> the two overlap: no single cosine floor separates absent-topic "
                  "queries from real ones")

    if args.json:
        print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
