#!/usr/bin/env python3
"""Day 5 step 3b: run the real keyword engine over the corpus and read the answers.

Everything before this measured. This executes docpulse.search.keyword - the module
the API will call - so what it prints is what /search?mode=keyword will return.

Three things to read:

  1. strict hit rate  - how often an AND query finds anything at all. The coverage
                        probe said specific queries match 1-17 papers; if strict
                        answers most queries, the corpus does not need fusion.
  2. answer set size  - a strict result of 1-8 rows is the exact-match experience
                        users actually want from a search box. 20+ means the query
                        was too broad for AND to be useful.
  3. timings          - strict should be a GIN bitmap; relaxed is a broader match.
                        Anything near the 1,271 ms of the inline query means the
                        stored column is not being used.

    make -f day5.mk keyword-probe
    make -f day5.mk keyword-probe PROBE_ARGS='--queries "quantization of neural networks"'
"""

from __future__ import annotations

import argparse
import json
import sys
import time

DEFAULT_QUERIES = (
    "sparse attention for long documents",
    "diffusion model image generation",
    "BERT pretraining masked language model",
    "retrieval augmented generation",
    "LoRA fine-tuning",
    "quantization of neural networks",
    "reinforcement learning from human feedback",
    "attention is all you need",
    "Yann LeCun",
    "Geoffrey Hinton",
    "cosmic microwave background power spectra",
    "protein structure prediction",
)

# A strict answer this small is the point of strict mode.
SHORT_ANSWER = 8


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the keyword engine (read-only)")
    parser.add_argument("--queries", nargs="*", default=None)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--category", default=None)
    parser.add_argument("--show", type=int, default=5)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    import psycopg

    from docpulse.embed.config import EmbeddingSettings
    from docpulse.search.keyword import search_keyword

    settings = EmbeddingSettings.from_env()
    queries = args.queries or list(DEFAULT_QUERIES)

    def timed(cur, query):
        best = None
        out = None
        for _ in range(3):
            started = time.perf_counter()
            out = search_keyword(cur, query, limit=args.limit, category=args.category)
            ms = (time.perf_counter() - started) * 1000
            best = ms if best is None else min(best, ms)
        return out, best

    report = []
    with psycopg.connect(settings.dsn, connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT version()")
            print(f"postgres   {cur.fetchone()[0].split(',')[0]}")
            cur.execute("SELECT count(*) FROM paper_meta")
            print(f"corpus     {int(cur.fetchone()[0])} papers")
            print(f"limit      {args.limit}   category {args.category or '(none)'}")
            print("")

            for query in queries:
                result, ms = timed(cur, query)
                rows = result["rows"]
                stems = result["stems"]
                mode = result["mode"]
                print(f"=== {query!r}")
                print(f"    stems    {' | '.join(stems) if stems else '(none)'}   "
                      f"({len(stems)} term(s))")
                label = {"strict": "strict ", "relaxed": "relaxed", "empty": "empty  "}[mode]
                print(f"    {label}  {len(rows):2d} row(s)   {ms:7.2f} ms"
                      + ("" if mode != "relaxed" else "   (strict found nothing)"))
                for rank, hit in enumerate(rows[: args.show], start=1):
                    print(f"    #{rank}  {hit['arxiv_id']:<12} "
                          f"{hit['coverage']}/{hit['total']}  {(hit['title'] or '')[:58]}")
                print("")
                report.append({
                    "query": query,
                    "mode": mode,
                    "stems": stems,
                    "ms": round(ms, 2),
                    "n": len(rows),
                    "rows": rows[: args.show],
                })

            # Is the GIN index dead weight at this size, or just not chosen? Ask the
            # planner directly, with the same query, seq scans disabled.
            selective = next((r for r in report if r["mode"] == "strict" and 0 < r["n"] <= 5), None)
            if selective:
                from docpulse.search.keyword import _strict_sql

                stems = selective["stems"]
                params = {"tsq": " & ".join(stems), "limit": args.limit, "category": None}
                print(f"-- plan for a selective strict query: {selective['query']!r} --")
                for label, setting in (("default", "on"), ("seqscan off", "off")):
                    cur.execute(f"SET enable_seqscan = {setting}")
                    started = time.perf_counter()
                    cur.execute("EXPLAIN (ANALYZE, BUFFERS) " + _strict_sql("relevance"), params)
                    plan = [r[0] for r in cur.fetchall()]
                    ms = (time.perf_counter() - started) * 1000
                    index = any("Index Scan" in line for line in plan)
                    print(f"   {label:<12} index scan: {'yes' if index else 'no ':<4} "
                          f"{ms:7.2f} ms")
                    for line in plan:
                        if "Scan" in line or "Execution Time" in line:
                            print("      " + line.strip())
                cur.execute("SET enable_seqscan = on")
                print("")

    strict = [r for r in report if r["mode"] == "strict"]
    relaxed = [r for r in report if r["mode"] == "relaxed"]
    empty = [r for r in report if r["mode"] == "empty"]
    print(f"strict answered      {len(strict)}/{len(report)} queries")
    if strict:
        sizes = [r["n"] for r in strict]
        short = [r for r in strict if r["n"] <= SHORT_ANSWER]
        print(f"  answer set sizes   {sorted(sizes)}")
        print(f"  <= {SHORT_ANSWER} rows       {len(short)}/{len(strict)} "
              f"(the exact-match experience)")
        if not relaxed and all(s <= args.limit for s in sizes):
            print("  note               every strict answer fit under --limit, so relaxed "
                  "never had to run")
    if relaxed:
        print(f"relaxed fallback     {len(relaxed)}: "
              f"{[r['query'] for r in relaxed]}")
    if empty:
        print(f"no match at all      {len(empty)}: {[r['query'] for r in empty]}")
        print("  (expected for author names: paper_meta has no authors column)")
    times = {m: [r["ms"] for r in report if r["mode"] == m] for m in ("strict", "relaxed")}
    for mode, values in times.items():
        if values:
            print(f"  {mode:<7} mean {sum(values) / len(values):7.2f} ms   max {max(values):7.2f} ms")

    if args.json:
        print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
