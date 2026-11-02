"""Day 4: configuration for the search API.

Deliberately thin: everything about the model, the database and the S3 store is
already decided by Day 3's ``EmbeddingSettings``. Search only adds what is
specific to serving requests (port, limits, cache size), so the two services can
never disagree about which model or which database they are using.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping

from ..embed.config import EmbeddingSettings

DEFAULT_PORT = 8080
DEFAULT_LIMIT = 10
MAX_LIMIT = 100
DEFAULT_CACHE_SIZE = 512
MIN_QUERY_CHARS = 1
MAX_QUERY_CHARS = 512


def _int(env: Mapping[str, str], name: str, default: int, lo: int, hi: int) -> int:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(lo, min(hi, value))


@dataclass(frozen=True)
class SearchSettings:
    embedding: EmbeddingSettings
    port: int = DEFAULT_PORT
    default_limit: int = DEFAULT_LIMIT
    max_limit: int = MAX_LIMIT
    table: str = "paper_embeddings"
    meta_table: str = "paper_meta"
    cache_size: int = DEFAULT_CACHE_SIZE
    host: str = "0.0.0.0"

    # -- derived ---------------------------------------------------------
    @property
    def dsn(self) -> str:
        return self.embedding.dsn

    @property
    def dim(self) -> int:
        return self.embedding.dim

    @property
    def model_id(self) -> str:
        return self.embedding.model_id

    @property
    def model_revision(self) -> str:
        return self.embedding.model_revision

    def clamp_limit(self, requested: int | None) -> int:
        """A client asking for 10_000 rows gets ``max_limit``, not a 500."""
        if requested is None:
            return self.default_limit
        try:
            value = int(requested)
        except (TypeError, ValueError):
            return self.default_limit
        if value < 1:
            return 1
        return min(value, self.max_limit)

    def public(self) -> dict[str, Any]:
        return {
            "port": self.port,
            "table": self.table,
            "meta_table": self.meta_table,
            "default_limit": self.default_limit,
            "max_limit": self.max_limit,
            "cache_size": self.cache_size,
            "dim": self.dim,
            "model_id": self.model_id,
            "model_revision": self.model_revision,
            "dsn": _redact(self.dsn),
        }

    # -- construction ----------------------------------------------------
    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "SearchSettings":
        source: Mapping[str, str] = dict(os.environ) if env is None else env
        default_limit = _int(source, "SEARCH_DEFAULT_LIMIT", DEFAULT_LIMIT, 1, MAX_LIMIT)
        max_limit = _int(source, "SEARCH_MAX_LIMIT", MAX_LIMIT, default_limit, 1000)
        return cls(
            embedding=EmbeddingSettings.from_env(source),
            port=_int(source, "SEARCH_PORT", DEFAULT_PORT, 1, 65535),
            default_limit=default_limit,
            max_limit=max_limit,
            table=(source.get("SEARCH_TABLE") or "paper_embeddings").strip(),
            meta_table=(source.get("SEARCH_META_TABLE") or "paper_meta").strip(),
            cache_size=_int(source, "SEARCH_CACHE_SIZE", DEFAULT_CACHE_SIZE, 0, 100_000),
            host=(source.get("SEARCH_HOST") or "0.0.0.0").strip(),
        )


def _redact(dsn: str) -> str:
    """``postgresql://user:pw@host:5432/db`` -> ``postgresql://user:***@host:5432/db``."""
    if "@" not in dsn:
        return dsn
    head, tail = dsn.split("@", 1)
    scheme = head.split("//", 1)[0] + "//" if "//" in head else ""
    user = head[len(scheme):].split(":", 1)[0]
    return f"{scheme}{user}:***@{tail}"
