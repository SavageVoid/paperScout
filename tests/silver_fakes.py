"""Helpers to build realistic bronze partitions for silver tests."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

from docpulse.storage import ObjectStore

UTC = timezone.utc
FEED_HEAD = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<feed xmlns="http://www.w3.org/2005/Atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/" '
    'xmlns:arxiv="http://arxiv.org/schemas/atom">\n'
    "<title>ArXiv Query</title><updated>2026-10-30T00:00:00Z</updated>\n"
    "<opensearch:totalResults>{total}</opensearch:totalResults>"
    "<opensearch:startIndex>0</opensearch:startIndex><opensearch:itemsPerPage>{n}</opensearch:itemsPerPage>\n"
)


def ts(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def rich_entry(
    arxiv_id: str = "2610.00001",
    version: int = 1,
    published: datetime = datetime(2026, 10, 20, 9, 10, 11, tzinfo=UTC),
    updated: datetime | None = None,
    title: str = "A  Study of\n    Things",
    abstract: str = "  We study things.\n  And more things. ",
    authors: tuple[str, ...] = ("Ada Lovelace", "Alan Turing"),
    primary: str | None = "cs.LG",
    categories: tuple[str, ...] = ("cs.LG", "stat.ML"),
    comment: str | None = "12 pages, 3 figures",
    extra: str = "",
    omit: tuple[str, ...] = (),
) -> str:
    updated = updated or published
    parts = [f"<id>http://arxiv.org/abs/{arxiv_id}v{version}</id>"]
    if "updated" not in omit:
        parts.append(f"<updated>{ts(updated) if isinstance(updated, datetime) else updated}</updated>")
    if "published" not in omit:
        parts.append(f"<published>{ts(published) if isinstance(published, datetime) else published}</published>")
    if "title" not in omit:
        parts.append(f"<title>{title}</title>")
    if "summary" not in omit:
        parts.append(f"<summary>{abstract}</summary>")
    for a in authors:
        parts.append(f"<author><name>{a}</name></author>")
    if comment:
        parts.append(f"<arxiv:comment>{comment}</arxiv:comment>")
    parts.append(f'<link href="http://arxiv.org/abs/{arxiv_id}v{version}" rel="alternate" type="text/html"/>')
    parts.append(f'<link title="pdf" href="http://arxiv.org/pdf/{arxiv_id}v{version}" rel="related" type="application/pdf"/>')
    if primary:
        parts.append(f'<arxiv:primary_category term="{primary}" scheme="http://arxiv.org/schemas/atom"/>')
    for c in categories:
        parts.append(f'<category term="{c}" scheme="http://arxiv.org/schemas/atom"/>')
    parts.append(extra)
    return "<entry>\n" + "\n".join(parts) + "\n</entry>"


def write_bronze(
    store: ObjectStore,
    day: date,
    entries: list[str],
    fetched_at: datetime = datetime(2026, 10, 30, 0, 30, tzinfo=UTC),
    per_page: int = 50,
    complete: bool = True,
) -> None:
    prefix = f"bronze/arxiv/ingest_date={day.isoformat()}/"
    names = []
    chunks = [entries[i : i + per_page] for i in range(0, len(entries), per_page)] or [[]]
    for i, chunk in enumerate(chunks):
        name = f"page-{i:05d}.xml"
        names.append({"name": name})
        body = FEED_HEAD.format(total=len(entries), n=len(chunk)) + "\n".join(chunk) + "\n</feed>"
        store.put_bytes(prefix + name, body.encode("utf-8"))
    if complete:
        manifest = {"complete": True, "ingest_date": day.isoformat(), "pages": names, "fetched_at": ts(fetched_at)}
        store.put_bytes(prefix + "_manifest.json", json.dumps(manifest).encode())


def n_papers(i: int, **kw) -> list[str]:
    """i distinct valid entries with ids 2610.00001 .. 2610.0000i."""
    return [rich_entry(arxiv_id=f"2610.{k:05d}", **kw) for k in range(1, i + 1)]
