"""Day 4: turning a query string into a vector, once per distinct query.

Encoding on CPU costs tens of milliseconds — fine for a single request, wasteful
when the same query arrives repeatedly (a UI typing/ahead, a benchmark loop, a
dashboard). The cache is keyed on the *normalised* text plus the model
fingerprint, so switching models can never serve a stale vector.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any, Sequence


def normalise_query(text: str) -> str:
    """Collapse whitespace; queries are matched against stripped abstracts."""
    return " ".join((text or "").split())


class QueryEncoder:
    def __init__(self, encoder: Any, *, cache_size: int = 512):
        self._encoder = encoder
        self._cache: OrderedDict[tuple[str, str], list[float]] = OrderedDict()
        self._lock = threading.Lock()
        self.cache_size = max(0, int(cache_size))
        self.hits = 0
        self.misses = 0

    @property
    def fingerprint(self) -> str:
        return getattr(self._encoder, "fingerprint", "")

    def encode(self, text: str) -> list[float]:
        key = (normalise_query(text), self.fingerprint)
        with self._lock:
            if self.cache_size and key in self._cache:
                self._cache.move_to_end(key)
                self.hits += 1
                return self._cache[key]
            self.misses += 1
        # encode outside the lock: the model is the slow part, and a miss must not
        # block every other request
        vector = list(self._encoder.encode([key[0]])[0])
        with self._lock:
            if self.cache_size:
                self._cache[key] = vector
                self._cache.move_to_end(key)
                while len(self._cache) > self.cache_size:
                    self._cache.popitem(last=False)
        return vector

    def stats(self) -> dict[str, Any]:
        with self._lock:
            total = self.hits + self.misses
            return {
                "size": len(self._cache),
                "capacity": self.cache_size,
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": round(self.hits / total, 3) if total else None,
                "fingerprint": self.fingerprint,
            }

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self.hits = 0
            self.misses = 0


def warmup(encoder: QueryEncoder, words: Sequence[str] = ("test",)) -> None:
    """Load the model before the first user request (startup cost, not latency)."""
    for word in words:
        encoder.encode(word)
