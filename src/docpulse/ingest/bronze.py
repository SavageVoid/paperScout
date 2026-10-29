"""Bronze ingestion: raw arXiv Atom pages, untouched, in object storage.

Layout::

    bronze/arxiv/ingest_date=YYYY-MM-DD/page-00000.xml   raw API responses, byte-for-byte
    bronze/arxiv/ingest_date=YYYY-MM-DD/_manifest.json   written LAST; its presence = complete

``ingest_date`` is the Airflow *logical* date (the day of data the run covers), not the
wall-clock time of the run. That is what makes reruns and backfills land in the same
partition and overwrite it instead of piling up duplicates.

How "new and updated papers" are found: the API's date filter only covers the original
submission date, so revisions (v2, v3, ...) would be invisible to it. Instead we sort by
``lastUpdatedDate`` descending and page until a page reaches entries older than the window.
Consequences (documented limitations):
  * the API pages at most ~30,000 results deep, so only windows within roughly the last
    few weeks are reachable this way; ``WindowTooDeepError`` is raised rather than
    silently returning a truncated partition;
  * while we page, newly updated papers push older ones down, so an entry may appear on two
    adjacent pages. Duplicates are possible, losses are not; silver dedupes by (id, version).
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from docpulse import __version__
from docpulse.config import ArxivSettings
from docpulse.ingest.arxiv_client import (
    MAX_RESULTS_PER_QUERY,
    FeedPage,
    FetchedPage,
    build_search_query,
    parse_feed,
)
from docpulse.ingest.schedule import freshness
from docpulse.storage import ObjectNotFound, ObjectStore
from docpulse.timeutil import as_utc

log = logging.getLogger(__name__)

BRONZE_PREFIX = "bronze/arxiv"
MANIFEST_NAME = "_manifest.json"
MANIFEST_SCHEMA_VERSION = 1
SORT_BY = "lastUpdatedDate"
SORT_ORDER = "descending"


class BronzeIngestError(RuntimeError):
    pass


class WindowTooDeepError(BronzeIngestError):
    pass


class EmptyWindowError(BronzeIngestError):
    """No entries in the window. With a multi-day lookback this means the source feed has
    stalled (or the lookback is too short), never "a quiet day", so we fail instead of
    publishing an empty partition that looks like success."""


class BronzeVerificationError(BronzeIngestError):
    pass


@dataclass(frozen=True)
class IngestWindow:
    """Half-open interval [start, end) of ``updated`` timestamps, both timezone-aware UTC."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        for name, value in (("start", self.start), ("end", self.end)):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"window {name} must be timezone-aware")
        if self.start >= self.end:
            raise ValueError(f"empty or inverted window: {self.start} >= {self.end}")

    @classmethod
    def for_interval(
        cls, interval_start: datetime, interval_end: datetime, lookback_hours: int = 0
    ) -> IngestWindow:
        """The fetch window for an Airflow data interval, widened backwards by ``lookback_hours``
        so papers that arXiv indexed late (after the previous run) are still caught."""
        start_utc, end_utc = as_utc(interval_start), as_utc(interval_end)
        # Validate the interval itself *before* widening it: otherwise an empty interval
        # (e.g. a manual trigger) would silently become a valid lookback-sized window.
        if start_utc >= end_utc:
            raise ValueError(f"empty or inverted data interval: {start_utc} >= {end_utc}")
        return cls(start=start_utc - timedelta(hours=lookback_hours), end=end_utc)

    def contains(self, ts: datetime) -> bool:
        return self.start <= ts < self.end


@dataclass(frozen=True)
class CollectedPage:
    start_index: int
    raw: bytes
    page: FeedPage


@dataclass(frozen=True)
class Collection:
    pages: list[CollectedPage]
    stop_reason: str  # "reached_window_start" | "exhausted_results"
    total_results_reported: int


def partition_prefix(ingest_date: date) -> str:
    return f"{BRONZE_PREFIX}/ingest_date={ingest_date.isoformat()}/"


def collect_pages(
    client: Any,
    settings: ArxivSettings,
    window: IngestWindow,
    *,
    max_offset: int = MAX_RESULTS_PER_QUERY,
) -> Collection:
    """Fetch pages (newest first) until the window start is covered. Nothing is written here,
    so a failure part-way leaves the previously published partition untouched."""
    query = build_search_query(settings.categories)
    pages: list[CollectedPage] = []
    start = 0
    total = 0
    while True:
        if len(pages) >= settings.max_pages:
            raise BronzeIngestError(
                f"hit max_pages={settings.max_pages} before reaching window start {window.start}; "
                "refusing to publish a truncated partition (raise ARXIV_MAX_PAGES if intended)"
            )
        if start >= max_offset:
            raise WindowTooDeepError(
                f"window {window.start}..{window.end} is deeper than the {max_offset} results the "
                "arXiv API will page through; it can no longer be reached via lastUpdatedDate sorting"
            )
        size = min(settings.page_size, max_offset - start)
        fetched: FetchedPage = client.fetch_page(
            search_query=query, start=start, max_results=size, sort_by=SORT_BY, sort_order=SORT_ORDER
        )
        page = fetched.page
        total = page.total_results
        pages.append(CollectedPage(start_index=start, raw=fetched.raw, page=page))
        log.info("page %d: start=%d entries=%d total=%d", len(pages) - 1, start, len(page.entries), total)

        if not page.entries:
            return Collection(pages, "exhausted_results", total)
        oldest = page.oldest_updated
        if oldest is not None and oldest < window.start:
            return Collection(pages, "reached_window_start", total)
        start += len(page.entries)
        if start >= total:
            return Collection(pages, "exhausted_results", total)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _iso(ts: datetime | None) -> str | None:
    return ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if ts else None


def build_manifest(
    *,
    ingest_date: date,
    window: IngestWindow,
    settings: ArxivSettings,
    collection: Collection,
    fetched_at: datetime,
) -> dict[str, Any]:
    page_rows = []
    in_window_ids: set[str] = set()
    n_in_window = 0
    n_total = 0
    n_bytes = 0
    newest_seen: datetime | None = None
    for i, cp in enumerate(collection.pages):
        page_rows.append(
            {
                "name": f"page-{i:05d}.xml",
                "start_index": cp.start_index,
                "n_entries": len(cp.page.entries),
                "bytes": len(cp.raw),
                "sha256": _sha256(cp.raw),
                "newest_updated": _iso(cp.page.newest_updated),
                "oldest_updated": _iso(cp.page.oldest_updated),
            }
        )
        n_total += len(cp.page.entries)
        n_bytes += len(cp.raw)
        if cp.page.newest_updated and (newest_seen is None or cp.page.newest_updated > newest_seen):
            newest_seen = cp.page.newest_updated
        for entry in cp.page.entries:
            if window.contains(entry.updated):
                n_in_window += 1
                in_window_ids.add(entry.base_id)
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "complete": True,
        "source": "arxiv_api",
        "docpulse_version": __version__,
        "ingest_date": ingest_date.isoformat(),
        "query": {
            "search_query": build_search_query(settings.categories),
            "sort_by": SORT_BY,
            "sort_order": SORT_ORDER,
            "page_size": settings.page_size,
        },
        "window": {
            "start": _iso(window.start),
            "end": _iso(window.end),
            "lookback_hours": settings.lookback_hours,
        },
        "stop_reason": collection.stop_reason,
        "total_results_reported": collection.total_results_reported,
        "n_pages": len(page_rows),
        "n_entries_total": n_total,
        "n_entries_in_window": n_in_window,
        "n_unique_papers_in_window": len(in_window_ids),
        "n_bytes_total": n_bytes,
        "freshness": freshness(newest_seen, fetched_at),
        "pages": page_rows,
        "fetched_at": _iso(fetched_at),
    }


def publish_partition(
    store: ObjectStore,
    ingest_date: date,
    window: IngestWindow,
    settings: ArxivSettings,
    collection: Collection,
    fetched_at: datetime,
) -> dict[str, Any]:
    prefix = partition_prefix(ingest_date)
    manifest = build_manifest(
        ingest_date=ingest_date, window=window, settings=settings,
        collection=collection, fetched_at=fetched_at,
    )
    # Guards run BEFORE anything is deleted, so a bad fetch never destroys a good partition.
    if manifest["n_entries_in_window"] == 0:
        raise EmptyWindowError(
            f"no entries between {window.start} and {window.end}; the arXiv feed looks stalled "
            f"(freshness: {manifest['freshness']}). Nothing was written."
        )
    if manifest["freshness"]["behind_schedule"]:
        log.warning("arXiv feed is behind its announcement schedule: %s", manifest["freshness"])
    # Overwrite semantics: clear the partition first (this removes the old manifest, so a
    # half-written partition is never mistaken for a complete one), then write pages, then
    # the manifest last.
    removed = store.delete_prefix(prefix)
    if removed:
        log.info("rerun: removed %d existing objects under %s", removed, prefix)
    for row, cp in zip(manifest["pages"], collection.pages, strict=True):
        store.put_bytes(prefix + row["name"], cp.raw, "application/atom+xml")
    store.put_bytes(
        prefix + MANIFEST_NAME,
        json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8"),
        "application/json",
    )
    return manifest


def ingest_window(
    client: Any,
    store: ObjectStore,
    settings: ArxivSettings,
    ingest_date: date,
    window: IngestWindow,
    now: datetime | None = None,
) -> dict[str, Any]:
    collection = collect_pages(client, settings, window)
    fetched_at = now or datetime.now(timezone.utc)
    return publish_partition(store, ingest_date, window, settings, collection, fetched_at)


def summarize_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """The small, stable summary that goes into XCom and the verify step."""
    return {
        "ingest_date": manifest["ingest_date"],
        "n_pages": manifest["n_pages"],
        "n_entries_total": manifest["n_entries_total"],
        "n_entries_in_window": manifest["n_entries_in_window"],
        "n_unique_papers_in_window": manifest["n_unique_papers_in_window"],
        "n_bytes_total": manifest["n_bytes_total"],
        "stop_reason": manifest["stop_reason"],
        "newest_updated_seen": manifest["freshness"]["newest_updated_seen"],
        "behind_schedule": manifest["freshness"]["behind_schedule"],
    }


def verify_partition(store: ObjectStore, ingest_date: date) -> dict[str, Any]:
    """Integrity check of a published partition: manifest present and complete, every page
    present with the recorded size/checksum/entry count, and no stray objects."""
    prefix = partition_prefix(ingest_date)
    try:
        manifest = json.loads(store.get_bytes(prefix + MANIFEST_NAME))
    except ObjectNotFound as exc:
        raise BronzeVerificationError(f"no manifest under {prefix}: partition incomplete") from exc
    if not manifest.get("complete"):
        raise BronzeVerificationError(f"manifest under {prefix} is not marked complete")

    expected = {prefix + MANIFEST_NAME}
    for row in manifest["pages"]:
        key = prefix + row["name"]
        expected.add(key)
        try:
            raw = store.get_bytes(key)
        except ObjectNotFound as exc:
            raise BronzeVerificationError(f"missing page {key}") from exc
        if len(raw) != row["bytes"] or _sha256(raw) != row["sha256"]:
            raise BronzeVerificationError(f"checksum/size mismatch for {key}")
        if len(parse_feed(raw).entries) != row["n_entries"]:
            raise BronzeVerificationError(f"entry count mismatch for {key}")
    stray = sorted(set(store.list_keys(prefix)) - expected)
    if stray:
        raise BronzeVerificationError(f"unexpected objects in partition: {stray[:3]}")
    return summarize_manifest(manifest)
