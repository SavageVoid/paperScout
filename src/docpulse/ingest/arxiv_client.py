"""Polite arXiv API client.

Rules this module enforces (arXiv API Terms of Use):
  * at most one request every 3 seconds, single connection -> ``RateLimiter`` spaces the
    *start* of every request, retries included; one ``requests.Session`` is reused.
  * a descriptive User-Agent with a contact address.

Behaviours learned the hard way with this API:
  * errors can arrive as HTTP 200 with an Atom entry whose id contains ``/api/errors``;
  * a page can occasionally come back empty although ``totalResults`` says there is more;
  * 429/503 mean "slow down": we back off and honour ``Retry-After``.
"""

from __future__ import annotations

import logging
import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from xml.etree.ElementTree import ParseError

import requests
from defusedxml import ElementTree as SafeET
from defusedxml.common import DefusedXmlException

from docpulse.config import ArxivSettings

log = logging.getLogger(__name__)

ATOM = "{http://www.w3.org/2005/Atom}"
OPENSEARCH = "{http://a9.com/-/spec/opensearch/1.1/}"
MAX_RESULTS_PER_QUERY = 30_000  # arXiv will not page deeper than this for a single query
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
MAX_RETRY_AFTER_S = 300.0


class ArxivError(Exception):
    pass


class ArxivAPIError(ArxivError):
    """Non-retryable: the API rejected the request (bad query, 4xx, error entry)."""


class ArxivTransientError(ArxivError):
    """Retryable: throttling, 5xx, truncated or empty-but-should-not-be responses."""


class ArxivParseError(ArxivTransientError):
    pass


class ArxivRetriesExhausted(ArxivError):
    pass


@dataclass(frozen=True)
class EntryStamp:
    arxiv_id: str  # versioned, e.g. "2610.01234v2" or "hep-th/9901001v1"
    updated: datetime  # timezone-aware UTC

    @property
    def base_id(self) -> str:
        return re.sub(r"v\d+$", "", self.arxiv_id)


@dataclass(frozen=True)
class FeedPage:
    total_results: int
    start_index: int
    items_per_page: int
    entries: tuple[EntryStamp, ...]

    @property
    def oldest_updated(self) -> datetime | None:
        return min((e.updated for e in self.entries), default=None)

    @property
    def newest_updated(self) -> datetime | None:
        return max((e.updated for e in self.entries), default=None)


@dataclass(frozen=True)
class FetchedPage:
    raw: bytes  # the untouched response body: this is what lands in bronze
    page: FeedPage
    attempts: int


def build_search_query(categories: tuple[str, ...] | list[str]) -> str:
    return " OR ".join(f"cat:{c}" for c in categories)


def _parse_ts(text: str | None) -> datetime:
    if not text:
        raise ArxivParseError("missing timestamp")
    try:
        dt = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ArxivParseError(f"bad timestamp {text!r}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _int_text(root: Any, tag: str) -> int:
    node = root.find(OPENSEARCH + tag)
    if node is None or node.text is None:
        raise ArxivParseError(f"feed is missing opensearch:{tag}")
    try:
        return int(node.text.strip())
    except ValueError as exc:
        raise ArxivParseError(f"opensearch:{tag} is not an integer: {node.text!r}") from exc


def parse_feed(xml_bytes: bytes) -> FeedPage:
    """Extract just what bronze needs (counts and per-entry id/updated). Full parsing is silver's job."""
    try:
        root = SafeET.fromstring(xml_bytes)
    except (ParseError, DefusedXmlException) as exc:
        raise ArxivParseError(f"response is not valid XML: {exc}") from exc
    if root.tag != ATOM + "feed":
        raise ArxivParseError(f"unexpected root element {root.tag!r}")

    entries = []
    for entry in root.findall(ATOM + "entry"):
        id_text = (entry.findtext(ATOM + "id") or "").strip()
        if "/api/errors" in id_text:
            summary = (entry.findtext(ATOM + "summary") or "").strip()
            raise ArxivAPIError(f"arXiv API error: {summary or id_text}")
        if "/abs/" not in id_text:
            raise ArxivParseError(f"unexpected entry id {id_text!r}")
        arxiv_id = id_text.split("/abs/", 1)[1]
        entries.append(EntryStamp(arxiv_id, _parse_ts(entry.findtext(ATOM + "updated"))))

    return FeedPage(
        total_results=_int_text(root, "totalResults"),
        start_index=_int_text(root, "startIndex"),
        items_per_page=_int_text(root, "itemsPerPage"),
        entries=tuple(entries),
    )


class RateLimiter:
    """Guarantees at least ``min_interval_s`` between the starts of consecutive requests."""

    def __init__(
        self,
        min_interval_s: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._interval = min_interval_s
        self._clock = clock
        self._sleep = sleep
        self._last: float | None = None

    def wait(self) -> None:
        if self._last is not None:
            remaining = self._last + self._interval - self._clock()
            if remaining > 0:
                self._sleep(remaining)
        self._last = self._clock()


def _retry_after_seconds(value: str | None) -> float | None:
    if value and value.strip().isdigit():
        return min(float(value.strip()), MAX_RETRY_AFTER_S)
    return None


class ArxivClient:
    def __init__(
        self,
        settings: ArxivSettings,
        session: Any | None = None,
        limiter: RateLimiter | None = None,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self._s = settings
        self._session = session if session is not None else requests.Session()
        self._limiter = limiter if limiter is not None else RateLimiter(settings.min_interval_s)
        self._sleep = sleep
        self._jitter = jitter

    def fetch_page(
        self,
        *,
        search_query: str,
        start: int,
        max_results: int,
        sort_by: str = "lastUpdatedDate",
        sort_order: str = "descending",
    ) -> FetchedPage:
        params = {
            "search_query": search_query,
            "start": start,
            "max_results": max_results,
            "sortBy": sort_by,
            "sortOrder": sort_order,
        }
        last_exc: Exception | None = None
        for attempt in range(1, self._s.max_attempts + 1):
            self._limiter.wait()
            retry_after: float | None = None
            try:
                resp = self._session.get(
                    self._s.base_url,
                    params=params,
                    headers={"User-Agent": self._s.user_agent},
                    timeout=self._s.request_timeout_s,
                )
                if resp.status_code in RETRYABLE_STATUS:
                    retry_after = _retry_after_seconds(resp.headers.get("Retry-After"))
                    raise ArxivTransientError(f"HTTP {resp.status_code}")
                if resp.status_code >= 400:
                    raise ArxivAPIError(f"HTTP {resp.status_code}: {resp.text[:200]!r}")
                raw = resp.content
                page = parse_feed(raw)
                if not page.entries and start < page.total_results:
                    raise ArxivTransientError(
                        f"empty page at start={start} although totalResults={page.total_results}"
                    )
                return FetchedPage(raw=raw, page=page, attempts=attempt)
            except (requests.RequestException, ArxivTransientError) as exc:
                last_exc = exc
                if attempt == self._s.max_attempts:
                    break
                delay = min(self._s.backoff_max_s, self._s.backoff_base_s * 2 ** (attempt - 1))
                delay *= 1 + 0.25 * self._jitter()
                if retry_after is not None:
                    delay = max(delay, retry_after)
                log.warning(
                    "arXiv request failed (attempt %d/%d, start=%d): %s; retrying in %.1fs",
                    attempt, self._s.max_attempts, start, exc, delay,
                )
                self._sleep(delay)
        raise ArxivRetriesExhausted(
            f"giving up after {self._s.max_attempts} attempts at start={start}: {last_exc}"
        ) from last_exc
