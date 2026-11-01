"""Turn one Atom <entry> element into a candidate silver row.

Parsing never raises on bad content: it extracts what it can and leaves missing/unparseable values
as None, and the contract validator decides what is acceptable. That keeps one rule book (the
contract) instead of scattering validation across the parser.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from xml.etree.ElementTree import Element, tostring

ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV = "{http://arxiv.org/schemas/atom}"
_ID_RE = re.compile(r"^(?P<base>\d{4}\.\d{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/\d{7})v(?P<version>\d+)$")
_WS_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class ParsedEntry:
    row: dict
    id_hint: str  # the raw <id> text, kept for quarantine records
    unknown_elements: tuple[str, ...]
    notes: tuple[str, ...]  # parse-time observations, e.g. an unparseable date string


def normalize_text(value: str | None) -> str | None:
    """Collapse all whitespace runs (arXiv titles/abstracts contain hard line breaks and indentation)
    to single spaces, strip, and apply Unicode NFC so equal text hashes equal."""
    if value is None:
        return None
    return unicodedata.normalize("NFC", _WS_RE.sub(" ", value)).strip()


def split_arxiv_id(text: str | None) -> tuple[str, int] | None:
    """'http://arxiv.org/abs/2610.01234v2' -> ('2610.01234', 2); None if not a recognised id."""
    if not text or "/abs/" not in text:
        return None
    m = _ID_RE.match(text.strip().split("/abs/", 1)[1])
    return (m.group("base"), int(m.group("version"))) if m else None


def parse_ts(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def text_hash(title: str | None, abstract: str | None) -> str | None:
    if not title or not abstract:
        return None
    return hashlib.sha256(f"{title}\n\n{abstract}".encode()).hexdigest()


def _local(tag: object) -> str | None:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else None  # None for XML comments etc.


def entry_to_xml(entry: Element) -> str:
    return tostring(entry, encoding="unicode")


def parse_entry(entry: Element, known_elements: frozenset[str]) -> ParsedEntry:
    notes: list[str] = []
    id_text = (entry.findtext(ATOM + "id") or "").strip()
    split = split_arxiv_id(id_text)

    def date_of(tag: str) -> datetime | None:
        raw = entry.findtext(ATOM + tag)
        parsed = parse_ts(raw)
        if raw and parsed is None:
            notes.append(f"unparseable {tag}: {raw.strip()[:40]!r}")
        return parsed

    title = normalize_text(entry.findtext(ATOM + "title"))
    abstract = normalize_text(entry.findtext(ATOM + "summary"))

    authors = []
    for author in entry.findall(ATOM + "author"):
        name = normalize_text(author.findtext(ATOM + "name"))
        if name:
            authors.append(name)

    categories: list[str] = []
    for cat in entry.findall(ATOM + "category"):
        term = (cat.get("term") or "").strip()
        if term and term not in categories:
            categories.append(term)
    primary_el = entry.find(ARXIV + "primary_category")
    primary = (primary_el.get("term") or "").strip() if primary_el is not None else None

    pdf_url = None
    for link in entry.findall(ATOM + "link"):
        if link.get("title") == "pdf" or link.get("type") == "application/pdf":
            pdf_url = (link.get("href") or "").strip() or None
            break

    base = split[0] if split else None
    row = {
        "arxiv_id": base,
        "version": split[1] if split else None,
        "title": title,
        "abstract": abstract,
        "authors": authors,
        "primary_category": primary or None,
        "categories": categories,
        "published": date_of("published"),
        "updated": date_of("updated"),
        "comment": normalize_text(entry.findtext(ARXIV + "comment")) or None,
        "journal_ref": normalize_text(entry.findtext(ARXIV + "journal_ref")) or None,
        "doi": normalize_text(entry.findtext(ARXIV + "doi")) or None,
        "pdf_url": pdf_url,
        "abs_url": f"https://arxiv.org/abs/{base}" if base else None,
        "text_hash": text_hash(title, abstract),
    }
    unknown = sorted({n for child in entry if (n := _local(child.tag)) and n not in known_elements})
    return ParsedEntry(row=row, id_hint=id_text, unknown_elements=tuple(unknown), notes=tuple(notes))
