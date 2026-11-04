#!/usr/bin/env python3
"""Day 6 stage 1: what author data does this corpus actually contain?

Read-only. It never writes to Postgres and never calls arXiv. It reads the bronze
layer -- from a directory or from the project's object store -- pulls author names
out, and answers three questions before we add a column or a query path:

  1. Is the data complete? How many papers have zero authors, and which?
  2. What shape are the names in? Accents, LaTeX escapes, commas, particles,
     single-token names, duplicate entries, absurdly long lists.
  3. Do the acceptance names from Day 5 exist here at all? "Yann LeCun" and
     "Geoffrey Hinton" returned nothing on Day 5, which we read as "no authors
     column". It may simply be "not in this corpus". If so, author search can be
     correct on day one and still return zero for both, and the acceptance test
     needs names the corpus actually contains.

Two sources, same report:

    # object store (SeaweedFS/S3), the way the pipeline actually stores data
    docker compose ... exec searchapi python /opt/docpulse/scripts/day6_authors_probe.py \
        --s3 --prefix bronze

    # a local directory, if the harvest landed on disk
    python3 scripts/day6_authors_probe.py --root data/bronze

    --json /tmp/authors.json    write {arxiv_id: [names]} for stage 6.2
"""

from __future__ import annotations

import argparse
import collections
import gzip
import io
import json
import pathlib
import re
import statistics
import sys
import xml.etree.ElementTree as ET
from xml.sax.saxutils import unescape

DEFAULT_CHECK = ("Yann LeCun", "Geoffrey Hinton", "Yoshua Bengio", "Ashish Vaswani")

PARTICLES = {"van", "von", "de", "der", "den", "da", "di", "du", "del", "della",
             "la", "le", "bin", "al", "el", "dos", "das", "ter", "ten"}

ARXIV_ID_RE = re.compile(
    r"(?:abs/)?([0-9]{4}\.[0-9]{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/[0-9]{7})(?:v[0-9]+)?"
)
ENTRY_SPLIT_RE = re.compile(r"<entry\b.*?</entry>", re.S)
AUTHOR_RE = re.compile(r"<author\b.*?<name>\s*(.*?)\s*</name>.*?</author>", re.S)
ID_RE = re.compile(r"<id>\s*(.*?)\s*</id>", re.S)


# --------------------------------------------------------------------------- #
# parsing shared by both sources
# --------------------------------------------------------------------------- #
def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _arxiv_id(text: str | None) -> str:
    if not text:
        return ""
    match = ARXIV_ID_RE.search(text.strip())
    return match.group(1) if match else text.strip()


def _clean(name: str) -> str:
    return " ".join(unescape(name).split())


def parse_entry(entry: ET.Element) -> tuple[str, list[str]]:
    arxiv_id, names = "", []
    for child in entry.iter():
        tag = _local(child.tag)
        if tag == "id" and not arxiv_id:
            arxiv_id = _arxiv_id(child.text)
        elif tag == "author":
            for sub in child.iter():
                if _local(sub.tag) == "name" and sub.text:
                    names.append(_clean(sub.text))
                    break
    return arxiv_id, [n for n in names if n]


def records_from_xml_text(text: str):
    """Strict parse first; fall back to per-entry regex for concatenated feeds."""
    # Buffer, don't stream: a harvest object holding several concatenated feeds
    # parses the first document and then raises "junk after document element".
    # Streaming would emit that first document and then the fallback would emit it
    # again, double-counting every paper in the object.
    try:
        parsed = []
        for _, element in ET.iterparse(io.StringIO(text), events=("end",)):
            if _local(element.tag) == "entry":
                parsed.append(parse_entry(element))
                element.clear()
        yield from parsed
        return
    except ET.ParseError:
        pass
    for fragment in ENTRY_SPLIT_RE.findall(text):
        arxiv_id, names = "", []
        found = ID_RE.search(fragment)
        if found:
            arxiv_id = _arxiv_id(found.group(1))
        for raw in AUTHOR_RE.findall(fragment):
            cleaned = _clean(raw)
            if cleaned:
                names.append(cleaned)
        yield arxiv_id, names


def records_from_json_text(text: str):
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                payload.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if isinstance(payload, dict):
        payload = payload.get("records") or payload.get("entries") or list(payload.values())
    for record in payload:
        if not isinstance(record, dict):
            continue
        arxiv_id = _arxiv_id(str(record.get("arxiv_id") or record.get("id") or ""))
        names = record.get("authors") or record.get("author") or []
        if isinstance(names, str):
            names = [n for n in re.split(r"[;,]| and ", names) if n.strip()]
        cleaned = []
        for item in names:
            if isinstance(item, dict):
                item = item.get("name") or ""
            name = _clean(str(item))
            if name:
                cleaned.append(name)
        yield arxiv_id, cleaned


def decode_blob(blob: bytes) -> str:
    if blob[:2] == b"\x1f\x8b":
        try:
            blob = gzip.decompress(blob)
        except OSError:
            return ""
    return blob.decode("utf-8", errors="replace")


def records_from_blob(blob: bytes):
    text = decode_blob(blob)
    if not text.strip():
        return
    head = text[:4096]
    if "<entry" in head or "<feed" in head or "<OAI-PMH" in head:
        yield from records_from_xml_text(text)
    else:
        yield from records_from_json_text(text)


def kind_of(blob: bytes) -> str:
    text = decode_blob(blob)[:4096]
    if "<entry" in text or "<feed" in text or "<OAI-PMH" in text:
        return "xml"
    if text.lstrip()[:1] in "{[":
        return "json"
    return "other"


# --------------------------------------------------------------------------- #
# source 1: local directory
# --------------------------------------------------------------------------- #
def discover(root: pathlib.Path) -> list[pathlib.Path]:
    if not root.exists():
        raise SystemExit(f"ERROR: {root} does not exist")
    return sorted(p for p in root.rglob("*") if p.is_file() and not p.name.startswith("."))


def records_from_path(path: pathlib.Path):
    try:
        yield from records_from_blob(path.read_bytes())
    except OSError as exc:
        print(f"  ! {path.name}: {exc}")


# --------------------------------------------------------------------------- #
# source 2: the project's object store
# --------------------------------------------------------------------------- #
def attach_store(store, keys, prefix, limit):
    print(f"bucket              {getattr(store, 'bucket', '?')}")
    print(f"keys under {prefix!r}  {len(keys)}")
    shown = keys[:20]
    for key in shown:
        print(f"  {key}")
    if len(keys) > len(shown):
        print(f"  ... {len(keys) - len(shown)} more")
    for key in keys[:limit]:
        try:
            blob = store.get_bytes(key)
        except Exception as exc:  # noqa: BLE001 - report and continue
            print(f"  ! {key}: {exc}")
            continue
        yield from records_from_blob(blob)


def open_s3():
    try:
        from docpulse.config import StorageSettings
        from docpulse.storage import S3Store
    except ImportError as exc:
        raise SystemExit(
            f"ERROR: cannot import the project's storage layer ({exc}).\n"
            "Run this inside the app container, where the package is installed:\n"
            "  docker compose ... exec searchapi python /opt/docpulse/scripts/"
            "day6_authors_probe.py --s3 --prefix bronze"
        ) from exc
    return S3Store.from_settings(StorageSettings.from_env())


# --------------------------------------------------------------------------- #
# report
# --------------------------------------------------------------------------- #
def bucket_name(n: int) -> str:
    if n == 0:
        return "0"
    if n == 1:
        return "1"
    if n <= 5:
        return "2-5"
    if n <= 20:
        return "6-20"
    return ">20"


def sample(items, limit: int = 8) -> str:
    items = list(items)
    shown = ", ".join(repr(i) for i in items[:limit])
    return shown + (f"  (+{len(items) - limit} more)" if len(items) > limit else "")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Report what author data the corpus holds")
    parser.add_argument("--root", default="data/bronze", help="local directory to scan")
    parser.add_argument("--s3", action="store_true", help="read from the project's object store")
    parser.add_argument("--prefix", default="bronze", help="key prefix inside the bucket")
    parser.add_argument("--max-objects", type=int, default=500, help="stop after N objects")
    parser.add_argument("--check", nargs="*", default=list(DEFAULT_CHECK), help="names to look for")
    parser.add_argument("--json", dest="json_out", help="write {arxiv_id: [names]} here")
    args = parser.parse_args(argv)

    papers: dict[str, list[str]] = {}
    seen = 0

    print("== sources ==")
    if args.s3:
        store = open_s3()
        keys = [k for k in store.list_keys(args.prefix) if not k.endswith("/")]
        if not keys:
            print(f"no keys under {args.prefix!r}. Try --prefix '' to list the bucket root.")
            return 2
        for arxiv_id, names in attach_store(store, keys, args.prefix, args.max_objects):
            seen += 1
            if arxiv_id:
                papers.setdefault(arxiv_id, names)
    else:
        root = pathlib.Path(args.root)
        files = discover(root)
        kinds = collections.Counter()
        for path in files:
            kinds[kind_of(path.read_bytes()[:65536])] += 1
        print(f"files under {root}: {len(files)}")
        for kind, count in kinds.most_common():
            print(f"  {kind:8s} {count}")
        for path in files:
            for arxiv_id, names in records_from_path(path):
                seen += 1
                if arxiv_id:
                    papers.setdefault(arxiv_id, names)

    if not papers:
        print("\nERROR: parsed but extracted 0 papers.")
        print("Paste the key/prefix listing above and the probe will be adapted to it.")
        return 2

    all_names = [n for names in papers.values() for n in names]
    sizes = [len(v) for v in papers.values()]
    covered = sum(1 for s in sizes if s)
    pct = 100.0 * covered / len(papers)

    print("\n== coverage ==")
    print(f"records read      {seen}")
    print(f"papers            {len(papers)}")
    print(f"with >=1 author   {covered}  ({pct:.1f}%)")
    print(f"distinct names    {len(set(all_names))}  of {len(all_names)} entries")
    empty = [k for k, v in papers.items() if not v]
    print(f"with 0 authors    {len(empty)}" + (f"  {sample(empty, 5)}" if empty else ""))
    print(f"GATE >=99%        {'PASS' if pct >= 99.0 else 'FAIL - fix extraction before 6.2'}")

    print("\n== authors per paper ==")
    hist = collections.Counter(bucket_name(s) for s in sizes)
    for key in ("0", "1", "2-5", "6-20", ">20"):
        if hist.get(key):
            print(f"  {key:6s} {hist[key]:5d}")
    print(f"  mean {statistics.mean(sizes):.2f}  median {statistics.median(sizes)}  "
          f"max {max(sizes)}")
    longest = max(papers.items(), key=lambda kv: len(kv[1]))
    print(f"  longest list: {longest[0]} with {len(longest[1])} authors")

    print("\n== name shapes (measure before deciding fold rules) ==")
    non_ascii = [n for n in all_names if any(ord(c) > 127 for c in n)]
    latex = [n for n in all_names if "{" in n or "}" in n or "\\" in n]
    commas = [n for n in all_names if "," in n]
    single = [n for n in all_names if " " not in n.strip()]
    particles = [n for n in all_names
                 if any(tok.strip(".").lower() in PARTICLES for tok in n.split()[:-1])]
    dup_in_paper = [k for k, v in papers.items() if len(v) != len(set(v))]
    by_lower = collections.defaultdict(set)
    for name in all_names:
        by_lower[name.lower()].add(name)
    case_variants = [sorted(v) for v in by_lower.values() if len(v) > 1]

    for label, items in (
        ("non-ASCII", non_ascii),
        ("LaTeX braces/backslash", latex),
        ("contains comma", commas),
        ("single token", single),
        ("particle (van/de/le...)", particles),
        ("papers with a repeated author", dup_in_paper),
        ("names differing only by case", case_variants),
    ):
        print(f"  {label:30s} {len(items):5d}  {sample(items)}")

    print("\n== most frequent authors (who will saturate the limit) ==")
    for name, count in collections.Counter(all_names).most_common(15):
        print(f"  {count:4d}  {name}")

    print("\n== names in --check ==")
    lowered = collections.Counter(n.lower() for n in all_names)
    verdicts = {}
    for wanted in args.check:
        key = " ".join(wanted.split()).lower()
        exact = lowered.get(key, 0)
        tokens = key.split()
        last = tokens[-1] if tokens else ""
        partial = sum(c for name, c in lowered.items() if last and last in name.split())
        verdicts[wanted] = (exact, partial)
        flag = "OK    " if exact else ("partial-only" if partial else "ABSENT")
        print(f"  {flag}  {wanted:22s} exact {exact:4d}   last-name matches {partial:4d}")

    print("\n== verdict ==")
    good = [n for n, (exact, _) in verdicts.items() if exact]
    if good:
        print(f"  usable acceptance names: {', '.join(good)}")
    else:
        print("  NONE of the check names appear as authors in this corpus.")
        print("  The Day-5 empties were not a missing column, they were a missing")
        print("  person. Pick acceptance names from the frequency table above.")
    if pct < 99.0:
        print(f"  coverage {pct:.1f}% is below the 99% gate: extraction needs fixing")
        print("  before the schema change, not after.")

    if args.json_out:
        pathlib.Path(args.json_out).write_text(json.dumps(papers, indent=0, sort_keys=True))
        print(f"\nwrote {len(papers)} papers -> {args.json_out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
