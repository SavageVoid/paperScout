"""Day 4: fakes so the API can be tested without Postgres, torch or the network."""

from __future__ import annotations

from datetime import datetime, timezone


def sample_rows(n: int = 3) -> list[dict]:
    out = []
    for i in range(n):
        out.append(
            {
                "arxiv_id": f"2601.0000{i}",
                "score": 0.9 - i * 0.1,
                "version": 1,
                "title": f"Paper {i}",
                "abstract": f"Abstract body for paper {i}.",
                "categories": "cs.LG cs.AI",
                "published": datetime(2026, 1, 1 + i, tzinfo=timezone.utc),
                "updated": datetime(2026, 1, 2 + i, tzinfo=timezone.utc),
            }
        )
    return out


class FakeSearchDb:
    """Records what the API asked for; returns canned rows."""

    def __init__(
        self,
        *,
        rows: list[dict] | None = None,
        n_vectors: int = 3,
        model_fps: list[str] | None = None,
        metadata_loaded: bool = True,
        search_error: Exception | None = None,
    ):
        self.rows = sample_rows() if rows is None else rows
        self.n_vectors = n_vectors
        self.model_fps = model_fps
        self.metadata_loaded = metadata_loaded
        self.search_error = search_error
        self.calls: list[dict] = []
        self.connected = False
        self.closed = False

    # lifecycle
    def connect(self):
        self.connected = True
        return self

    def close(self):
        self.closed = True

    # queries
    def search(self, **kwargs):
        self.calls.append(kwargs)
        if self.search_error is not None:
            raise self.search_error
        return self.rows[: kwargs["limit"]]

    def paper(self, arxiv_id):
        for row in self.rows:
            if row["arxiv_id"] == arxiv_id:
                out = dict(row)
                out.pop("score", None)
                out["has_vector"] = True
                out["model_fp"] = "fake"
                return out
        return None

    def health(self, model_fp: str) -> dict:
        return {
            "db": "up",
            "n_vectors": self.n_vectors,
            "n_meta": len(self.rows) if self.metadata_loaded else 0,
            "metadata_loaded": self.metadata_loaded,
            "model_matches": (self.model_fps or [model_fp]) == [model_fp],
            "model_fps": self.model_fps or [model_fp],
            "took_ms": 0.0,
        }

    def stats(self, model_fp: str) -> dict:
        return {
            "n_rows": self.n_vectors,
            "n_papers": self.n_vectors,
            "avg_dims": 8.0,
            "n_meta": len(self.rows) if self.metadata_loaded else 0,
            "model_fp": model_fp,
        }
