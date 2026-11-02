"""Recover how silver computes ``text_hash``, empirically, from the data itself.

Day 3 trusts the stored ``text_hash`` to decide whether a paper changed. That is
only sound if the hash covers every field that changes the embedding. If silver
hashes only the abstract, then a title-only edit keeps the same hash, Day 3 sees
"unchanged", and the stored vector silently describes the old title.

Reading ``silver/build.py`` answers what the code *intends*; this module answers
what the data actually *contains*, by testing plausible constructions against
real rows. It is read-only and never writes.

Usage from the audit script::

    from docpulse.embed.hashaudit import identify
    report = identify(rows)          # rows: raw silver dicts
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable, Mapping, Sequence

__all__ = ["identify", "candidates", "describe"]


# --------------------------------------------------------------------------
# what a "recipe" is: which fields, joined how, hashed with what
# --------------------------------------------------------------------------
FIELD_SETS: tuple[tuple[str, ...], ...] = (
    ("abstract",),
    ("title",),
    ("title", "abstract"),
    ("abstract", "title"),
    ("title", "abstract", "categories"),
    ("title", "abstract", "primary_category"),
    ("categories", "title", "abstract"),
    ("primary_category", "title", "abstract"),
    ("arxiv_id", "title", "abstract"),
    ("title", "abstract", "authors"),
    ("arxiv_id", "version", "title", "abstract"),
    ("title", "abstract", "published"),
)

SEPARATORS: tuple[str, ...] = ("\n\n", "\n", " ", "", "\x00", "|")
SUFFIXES: tuple[str, ...] = ("", "\n")

HASHERS: dict[str, Any] = {
    "sha256": lambda b: hashlib.sha256(b).hexdigest(),
    "sha1": lambda b: hashlib.sha1(b).hexdigest(),
    "md5": lambda b: hashlib.md5(b).hexdigest(),
    "sha512": lambda b: hashlib.sha512(b).hexdigest(),
    "sha224": lambda b: hashlib.sha224(b).hexdigest(),
    "blake2b": lambda b: hashlib.blake2b(b, digest_size=32).hexdigest(),
}

VARIANTS: tuple[str, ...] = ("raw", "strip", "collapse", "lower")

_WS = re.compile(r"\s+")


def _variant(name: str, text: str) -> str:
    if name == "raw":
        return text
    if name == "strip":
        return text.strip()
    if name == "collapse":
        return _WS.sub(" ", text).strip()
    if name == "lower":
        return _WS.sub(" ", text).strip().lower()
    raise ValueError(f"unknown variant {name!r}")


def candidates() -> Iterable[dict[str, Any]]:
    """Every recipe we are willing to believe, in a deterministic order."""
    for fields in FIELD_SETS:
        for sep in SEPARATORS:
            for suffix in SUFFIXES:
                for pstrip in (True, False):
                    for variant in VARIANTS:
                        for hname, hfunc in HASHERS.items():
                            yield {
                                "fields": fields,
                                "sep": sep,
                                "suffix": suffix,
                                "strip_fields": pstrip,
                                "variant": variant,
                                "hasher": hname,
                                "_func": hfunc,
                            }


def describe(spec: Mapping[str, Any]) -> str:
    """Human-readable one-liner for a matched recipe."""
    parts = []
    for f in spec["fields"]:
        parts.append(f"{f}.strip()" if spec["strip_fields"] else f)
    body = f"{spec['sep']!r}.join([{', '.join(parts)}])"
    if spec["suffix"]:
        body += f" + {spec['suffix']!r}"
    if spec["variant"] != "raw":
        body = f"{spec['variant']}({body})"
    return f"{spec['hasher']}({body}.encode())"


def _value(row: Mapping[str, Any], field: str, pstrip: bool) -> str:
    val = row.get(field)
    if val is None:
        return ""
    text = val if isinstance(val, str) else str(val)
    return text.strip() if pstrip else text


def _hash_of(row: Mapping[str, Any], spec: Mapping[str, Any]) -> str | None:
    try:
        raw = spec["sep"].join(_value(row, f, spec["strip_fields"]) for f in spec["fields"])
    except Exception:
        return None
    text = _variant(spec["variant"], raw) + spec["suffix"]
    try:
        return spec["_func"](text.encode("utf-8"))
    except Exception:
        return None


# --------------------------------------------------------------------------
# the actual inference
# --------------------------------------------------------------------------
def _usable(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        stored = row.get("text_hash")
        if not stored or not isinstance(stored, str):
            continue
        if not (row.get("title") or row.get("abstract")):
            continue
        out.append(dict(row))
    return out


def identify(
    rows: Sequence[Mapping[str, Any]],
    sample: int = 200,
) -> dict[str, Any]:
    """Return a report describing which recipe reproduces ``text_hash``.

    Two passes: cheap screen on ``sample`` rows, then confirm survivors on every
    row, so a coincidence on 200 rows cannot masquerade as a match.
    """
    usable = _usable(rows)
    report: dict[str, Any] = {
        "n_rows_seen": len(rows),
        "n_rows_usable": len(usable),
        "n_candidates_tried": 0,
        "matches": [],
        "recipe": None,
        "covers_title": None,
        "covers_categories": None,
        "verdict": "not_identified",
        "n_identical_hashes_different_text": 0,
        "hash_length": None,
    }
    if not usable:
        report["verdict"] = "no_rows_with_text_hash"
        return report

    lengths = sorted({len(r["text_hash"]) for r in usable})
    report["hash_length"] = lengths

    # a hash that ignores a field collapses rows that differ only in that field
    seen: dict[str, tuple[str, str]] = {}
    for row in usable:
        stored = row["text_hash"]
        pair = (str(row.get("title") or ""), str(row.get("abstract") or ""))
        if stored in seen and seen[stored] != pair:
            report["n_identical_hashes_different_text"] += 1
        else:
            seen.setdefault(stored, pair)

    screen = usable[: max(1, min(sample, len(usable)))]
    survivors: list[dict[str, Any]] = []
    tried = 0
    for spec in candidates():
        tried += 1
        if all(_hash_of(row, spec) == row["text_hash"] for row in screen):
            survivors.append(spec)
    report["n_candidates_tried"] = tried

    confirmed = [
        spec
        for spec in survivors
        if all(_hash_of(row, spec) == row["text_hash"] for row in usable)
    ]
    report["matches"] = [
        {k: (list(v) if isinstance(v, tuple) else v) for k, v in spec.items() if k != "_func"}
        for spec in confirmed
    ]
    if not confirmed:
        report["verdict"] = "not_identified"
        report["hint"] = (
            "No simple field-join reproduces text_hash. The recipe may use a "
            "different normalisation (e.g. JSON of a record) or a non-hex digest. "
            "Send me the hashing function in silver/build.py."
        )
        return report

    # prefer the recipe that mentions the most fields: if two match, the data
    # cannot distinguish them and the wider one is the safer story
    best = max(confirmed, key=lambda s: (len(s["fields"]), s["hasher"] == "sha256"))
    report["recipe"] = describe(best)
    report["covers_title"] = "title" in best["fields"]
    report["covers_categories"] = bool({"categories", "primary_category"} & set(best["fields"]))
    report["n_matching_recipes"] = len(confirmed)
    report["verdict"] = "identified"
    if not report["covers_title"]:
        report["warning"] = (
            "B4 CONFIRMED: text_hash does not cover the title. A title-only edit "
            "will not be re-embedded by Day 3 - the vector keeps describing the "
            "old title. Widen the hash in silver/build.py, or add the title to the "
            "hash Day 3 compares."
        )
    elif report["n_identical_hashes_different_text"]:
        report["warning"] = (
            "text_hash includes the title but some rows share a hash with "
            "different text - inspect those rows."
        )
    return report
