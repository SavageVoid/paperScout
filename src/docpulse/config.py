"""Environment-driven settings with validation.

Validation is deliberate: the arXiv API terms ask for no more than one request every
three seconds, so a configuration that would break that is rejected at startup rather
than discovered as an IP ban later.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from docpulse import __version__
from docpulse.ingest.schedule import KNOWN_SKIPPED_ANNOUNCEMENTS

MIN_ARXIV_INTERVAL_S = 3.0  # arXiv Terms of Use: one request / 3 s, single connection
MAX_PAGE_SIZE = 2000  # arXiv API hard limit per request
DEFAULT_CONTACT = "unset@example.com"
_CATEGORY_RE = re.compile(r"^[A-Za-z][A-Za-z\-]*(\.[A-Za-z\-]+)?$")


class ConfigError(ValueError):
    """Raised when settings are invalid."""


@dataclass(frozen=True)
class ArxivSettings:
    base_url: str = "https://export.arxiv.org/api/query"
    categories: tuple[str, ...] = ("cs.LG", "cs.CL", "cs.AI")
    page_size: int = 200
    min_interval_s: float = 3.1  # 3.0 is the rule; 0.1 s of safety margin
    max_pages: int = 100  # safety valve: ~20k records at page_size=200
    # arXiv publishes in batches: an entry's `updated` (submission time) can precede its public
    # visibility by ~78 h over a weekend and ~126 h over long holidays. 144 h covers both; the
    # overlap is re-fetched every run and silver dedupes it.
    lookback_hours: int = 144
    request_timeout_s: float = 60.0
    max_attempts: int = 5
    backoff_base_s: float = 5.0
    backoff_max_s: float = 120.0
    contact_email: str = DEFAULT_CONTACT
    skipped_announcements: frozenset[date] = KNOWN_SKIPPED_ANNOUNCEMENTS

    def __post_init__(self) -> None:
        if self.min_interval_s < MIN_ARXIV_INTERVAL_S:
            raise ConfigError(
                f"min_interval_s={self.min_interval_s} violates the arXiv API terms "
                f"(>= {MIN_ARXIV_INTERVAL_S}s between requests)"
            )
        if not 1 <= self.page_size <= MAX_PAGE_SIZE:
            raise ConfigError(f"page_size must be in [1, {MAX_PAGE_SIZE}]")
        if self.max_pages < 1:
            raise ConfigError("max_pages must be >= 1")
        if self.lookback_hours < 0:
            raise ConfigError("lookback_hours must be >= 0")
        if self.max_attempts < 1:
            raise ConfigError("max_attempts must be >= 1")
        if not self.categories:
            raise ConfigError("at least one category is required")
        for cat in self.categories:
            if not _CATEGORY_RE.match(cat):
                raise ConfigError(f"invalid arXiv category: {cat!r}")

    @property
    def user_agent(self) -> str:
        return f"DocPulse/{__version__} (research pipeline; mailto:{self.contact_email})"

    @property
    def has_contact_email(self) -> bool:
        return not self.contact_email.lower().endswith("@example.com")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ArxivSettings:
        env = os.environ if env is None else env
        kwargs: dict[str, Any] = {}

        def put(field: str, key: str, cast: Any) -> None:
            raw = env.get(key)
            if raw in (None, ""):
                return
            try:
                kwargs[field] = cast(raw)
            except ValueError as exc:
                raise ConfigError(f"{key}={raw!r} is invalid: {exc}") from exc

        put("base_url", "ARXIV_BASE_URL", str)
        put("categories", "ARXIV_CATEGORIES", lambda v: tuple(c.strip() for c in v.split(",") if c.strip()))
        put("page_size", "ARXIV_PAGE_SIZE", int)
        put("min_interval_s", "ARXIV_MIN_INTERVAL_S", float)
        put("max_pages", "ARXIV_MAX_PAGES", int)
        put("lookback_hours", "ARXIV_LOOKBACK_HOURS", int)
        put("contact_email", "ARXIV_CONTACT_EMAIL", str)
        put(
            "skipped_announcements",
            "ARXIV_SKIPPED_ANNOUNCEMENTS",  # extra ET dates, comma-separated, added to the known ones
            lambda v: KNOWN_SKIPPED_ANNOUNCEMENTS
            | frozenset(date.fromisoformat(d.strip()) for d in v.split(",") if d.strip()),
        )
        return cls(**kwargs)


@dataclass(frozen=True)
class StorageSettings:
    bucket: str = "docpulse"
    endpoint_url: str | None = None  # None -> real AWS S3
    region: str = "us-east-1"
    access_key: str | None = None
    secret_key: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> StorageSettings:
        env = os.environ if env is None else env
        return cls(
            bucket=env.get("S3_BUCKET") or "docpulse",
            endpoint_url=env.get("S3_ENDPOINT_URL") or None,
            region=env.get("S3_REGION") or "us-east-1",
            access_key=env.get("AWS_ACCESS_KEY_ID") or None,
            secret_key=env.get("AWS_SECRET_ACCESS_KEY") or None,
        )
