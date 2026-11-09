#!/usr/bin/env python3
"""Build a golden question set out of the live corpus.

Invented arXiv IDs are the classic way to ship an eval that measures nothing: the query
and the expected answer get written by the same person from the same memory, so the eval
agrees with the model by construction. This samples real papers instead, and derives each
query from the paper's own text — the expected ID is then true by definition.

Three kinds are produced per sampled paper:

  title     the exact title, which keyword search should win (it is a literal phrase)
  phrase    an 8-word window from the middle of the abstract, distinctive by construction
  author    first author plus a topical word from the title, which needs author search

Sampling is deterministic (`order by md5(arxiv_id)`) so the set is reproducible and does
not drift every time it runs.

    python3 scripts/day8_golden_from_corpus.py --n 60
    python3 scripts/day8_golden_from_corpus.py --n 60 --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

COMPOSE = "docker compose -f docker-compose.yml -f docker-compose.day3.yml -f docker-compose.day4.yml"

SQL = """
select arxiv_id,
       title,
       (authors)[1] as first_author,
       left(abstract, 900) as abstract
from paper_meta
where array_length(authors, 1) between 1 and 3
  and char_length(abstract) > 500
  and char_length(title) > 25
order by md5(arxiv_id)
limit {n}
"""

STOPWORDS = {
    "a", "an", "the", "of", "for", "and", "or", "with", "in", "on", "to", "via",
    "using", "towards", "toward", "from", "by", "at", "as", "is", "are", "we",
    "this", "that", "these", "those", "its", "their", "can", "does", "not", "be",
}


def fetch_rows(n: int, compose: str) -> list[tuple[str, str, str, str]]:
    cmd = compose.split() + [
        "exec", "-T", "postgres", "psql", "-U", "docpulse", "-d", "docpulse_embed",
        "-A", "-F", "\x1f", "-t", "-c", SQL.format(n=n),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise SystemExit(
            "psql failed — is the stack up?\n" + (proc.stderr or proc.stdout)[-800:]
        )
    rows = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\x1f")
        if len(parts) != 4:
            continue
        rows.append((parts[0].strip(), parts[1].strip(), parts[2].strip(), parts[3].strip()))
    return rows


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def title_query(title: str) -> str:
    """The title without its trailing punctuation, which nobody types."""
    return clean(title).rstrip(" .:;,")


def phrase_query(abstract: str) -> str | None:
    """Eight *consecutive* words from the middle of the abstract.

    Contiguity is the whole point: a phrase query is only a fair test of keyword search
    if the words actually appear together in the source text. Dropping stopwords would
    make the "phrase" a bag of words no document contains.
    """
    words = clean(abstract).split()
    if len(words) < 40:
        return None
    start = max(0, len(words) // 2 - 8)
    window = words[start : start + 8]
    if len(window) < 8:
        return None
    return " ".join(window).strip(" ,.;:")


def author_query(first_author: str, title: str) -> tuple[str, str] | None:
    """Return (display query, surname the API is actually asked for).

    The API ignores `q=` in author mode, so the honest query is the surname alone; the
    topical word is kept only as a human-readable label in the golden file.
    """
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z\-]{3,}", title) if w.lower() not in STOPWORDS]
    if not first_author:
        return None
    surname = clean(first_author)  # author mode matches full names; a surname alone picks the wrong people
    if len(surname) < 3:
        return None
    label = f"{surname} {words[0].lower()}" if words else surname
    return label, surname


def build(rows: list[tuple[str, str, str, str]]) -> list[dict]:
    items: list[dict] = []
    seen: set[str] = set()

    def add(kind: str, label: str, arxiv_id: str, param: dict) -> None:
        key = f"{kind}:{label.lower()}"
        if key in seen:
            return
        seen.add(key)
        items.append({"kind": kind, "query": label, "param": param, "expect": [arxiv_id]})

    for arxiv_id, title, author, abstract in rows:
        t = title_query(title)
        add("title", t, arxiv_id, {"q": t})
        phrase = phrase_query(abstract)
        if phrase:
            add("phrase", phrase, arxiv_id, {"q": phrase})
        aq = author_query(author, title)
        if aq:
            label, surname = aq
            add("author", label, arxiv_id, {"author": surname})
    return items


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="derive a golden set from the corpus")
    parser.add_argument("--n", type=int, default=60, help="papers to sample")
    parser.add_argument("--out", default="eval/golden.json")
    parser.add_argument("--compose", default=COMPOSE)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--dump", type=int, default=0, help="print sampled papers")
    args = parser.parse_args(argv)

    rows = fetch_rows(args.n, args.compose)
    if not rows:
        raise SystemExit("no rows came back — check the database name and the sample filters")

    items = build(rows)
    counts: dict[str, int] = {}
    for item in items:
        counts[item["kind"]] = counts.get(item["kind"], 0) + 1

    print(f"sampled {len(rows)} papers → {len(items)} queries {counts}")
    for item in items[:3]:
        print(f"  [{item['kind']}] {item['query'][:70]}  →  {item['expect'][0]}")

    for arxiv_id, title, author, abstract in rows[: args.dump]:
        print(f"\n{arxiv_id} · {author}\n  {clean(title)}\n  {clean(abstract)[:260]}")

    if args.dry_run:
        print("--dry-run: nothing written.")
        return 0

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated": datetime.now(UTC).isoformat(timespec="seconds"),
        "sampled_papers": len(rows),
        "items": items,
    }
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(items)} queries)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
