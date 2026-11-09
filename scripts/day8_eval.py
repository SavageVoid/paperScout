#!/usr/bin/env python3
"""Score retrieval against the golden set. Standard library only.

Prints, and can write, one table per mode and kind:

  n     how many queries of that kind were run
  hit@1 fraction where the expected paper was ranked first
  recall@k  mean fraction of expected IDs found in the top k
  MRR   mean reciprocal rank of the first expected ID (0 when missed)
  ms    mean client-side latency

The point is not a single headline number. It is the per-kind split: keyword search should
own exact titles, vector search should own paraphrased phrases, and author search should
return one paper rather than a page. If vector loses on phrases, something is wrong with
the embeddings, and that is information the RAG layer cannot give you later.

Response parsing is deliberately tolerant about the array key and the id field, because a
scorer that silently scores zero because of a renamed field is worse than no scorer.

    python3 scripts/day8_eval.py --golden eval/golden.json --write EVAL.md
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

LIST_KEYS = ("hits", "results", "rows", "items", "papers", "data")
ID_KEYS = ("arxiv_id", "arxivId", "id", "paper_id")

MODE_FOR_KIND = {"title": ("keyword", "vector"), "phrase": ("vector", "keyword"), "paraphrase": ("vector", "keyword"), "author": ("author",)}


def build_url(base: str, item: dict, mode: str, k: int) -> str:
    """Author mode gets `author=`, everything else gets `q=`.

    Items built by day8_golden_from_corpus.py carry an explicit `param` dict, so the
    scorer never has to guess which token was the surname.
    """
    params = {"mode": mode, "limit": str(k)}
    param = item.get("param") or {}
    if mode == "author":
        # 422: "mode must be one of vector, keyword" -- author is inferred
        params.pop("mode", None)
        params["author"] = param.get("author") or item["query"].split()[0]
    else:
        params["q"] = param.get("q") or item["query"]
    return f"{base.rstrip('/')}/search?" + urllib.parse.urlencode(params)


def fetch(url: str, timeout: float) -> tuple[dict, float]:
    started = time.perf_counter()
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - local URL
        body = resp.read()
    ms = (time.perf_counter() - started) * 1000.0
    return json.loads(body), ms


def hits(payload: dict) -> list[dict]:
    for key in LIST_KEYS:
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
    return []


def row_id(row: dict) -> str:
    for key in ID_KEYS:
        value = row.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def score(ids: list[str], expect: list[str], k: int) -> tuple[float, float, int]:
    """recall@k, reciprocal rank of the first expected id, and its 1-based rank."""
    top = ids[:k]
    found = sum(1 for e in expect if e in top)
    recall = found / len(expect) if expect else 0.0
    rank = 0
    for i, got in enumerate(top, start=1):
        if got in expect:
            rank = i
            break
    rr = 1.0 / rank if rank else 0.0
    return recall, rr, rank


def run(goldens: list[dict], base: str, k: int, timeout: float) -> dict:
    results: dict[tuple[str, str], dict] = {}
    try:  # one discarded request, so the first timed query is not the cold start
        fetch(f"{base.rstrip('/')}/search?mode=vector&q=warmup&limit=1", timeout)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        pass
    misses: list[dict] = []
    errors: list[str] = []

    for item in goldens:
        kind = item.get("kind", "title")
        expect = item.get("expect") or []
        for mode in MODE_FOR_KIND.get(kind, ("keyword",)):
            url = build_url(base, item, mode, k)
            key = (kind, mode)
            bucket = results.setdefault(
                key, {"n": 0, "hit1": 0, "recall": [], "rr": [], "ms": [], "empty": 0}
            )
            try:
                payload, ms = fetch(url, timeout)
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                errors.append(f"{mode}: {item['query'][:50]} → {exc}")
                continue
            ids = [row_id(row) for row in hits(payload)]
            recall, rr, rank = score(ids, expect, k)
            bucket["n"] += 1
            bucket["hit1"] += 1 if rank == 1 else 0
            bucket["recall"].append(recall)
            bucket["rr"].append(rr)
            bucket["ms"].append(ms)
            if not ids:
                bucket["empty"] += 1
            if recall == 0.0:
                misses.append(
                    {"kind": kind, "mode": mode, "query": item["query"],
                     "expect": expect, "got": ids[:5]}
                )
    return {"results": results, "misses": misses, "errors": errors}


def table(results: dict) -> str:
    lines = [
        "| kind | mode | n | hit@1 | recall@k | MRR | mean ms | empty |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for (kind, mode), b in sorted(results.items()):
        n = b["n"]
        if not n:
            continue
        lines.append(
            f"| {kind} | {mode} | {n} | {b['hit1'] / n:.2f} | "
            f"{statistics.fmean(b['recall']):.3f} | {statistics.fmean(b['rr']):.3f} | "
            f"{statistics.fmean(b['ms']):.1f} | {b['empty']} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="score retrieval against the golden set")
    parser.add_argument("--golden", default="eval/golden.json")
    parser.add_argument("--base", default="http://localhost:8090")
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--write", default="", help="markdown file to write, e.g. EVAL.md")
    args = parser.parse_args(argv)

    path = Path(args.golden)
    if not path.is_file():
        raise SystemExit(f"{path} not found — build it first with day8_golden_from_corpus.py")

    goldens = json.loads(path.read_text(encoding="utf-8")).get("items", [])
    if not goldens:
        raise SystemExit(f"{path} contains no items")

    run_out = run(goldens, args.base, args.k, args.timeout)
    report = table(run_out["results"])
    print(report)

    if run_out["errors"]:
        print(f"{len(run_out['errors'])} request errors, first 3:")
        for line in run_out["errors"][:3]:
            print("  " + line)

    print(f"{len(run_out['misses'])} queries with recall 0 at k={args.k}")
    for miss in run_out["misses"][:5]:
        print(f"  [{miss['mode']}] {miss['query'][:60]} → expected {miss['expect'][0]}")

    if args.write:
        out = Path(args.write)
        body = [
            "# Retrieval evaluation",
            "",
            f"Golden set: {len(goldens)} queries, k={args.k}, base `{args.base}`.",
            "",
            report,
        ]
        if run_out["misses"]:
            body += ["## Misses (recall 0)", "", "| kind | mode | query | expected | got |", "|---|---|---|---|---|"]
            for miss in run_out["misses"][:40]:
                body.append(
                    f"| {miss['kind']} | {miss['mode']} | {miss['query'][:60]} | "
                    f"{miss['expect'][0]} | {', '.join(miss['got'][:3]) or '—'} |"
                )
        out.write_text("\n".join(body) + "\n", encoding="utf-8")
        print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
