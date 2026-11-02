"""Test doubles for the Day 3 embedding layer.

No pytest fixtures and no conftest.py on purpose: this file must not collide with
whatever the Day 1/2 test suite already has. Import it directly
(`from _day3_fakes import FakeDb`) — the Makefile already puts tests/ on
PYTHONPATH, and `unittest discover -s tests` does the same.
"""

from __future__ import annotations

import json
import math
from contextlib import contextmanager

from docpulse.embed.compat import SilverSnapshot

# Use the project's real manifest key when the Day 2 code is importable, so these
# tests exercise the actual lookup path rather than a guess.
try:  # pragma: no cover - depends on the repo
    from docpulse.silver.build import MANIFEST_KEY as SILVER_MANIFEST_KEY  # type: ignore
except Exception:  # pragma: no cover
    SILVER_MANIFEST_KEY = "silver/papers/_manifest.json"


class FakeStore:
    """Minimal in-memory stand-in for S3Store."""

    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self.objects: dict[str, bytes] = dict(objects or {})
        self.puts: list[str] = []
        self.deletes: list[str] = []

    def list_keys(self, prefix: str = "") -> list[str]:
        return sorted(k for k in self.objects if k.startswith(prefix))

    def get_bytes(self, key: str) -> bytes:
        return self.objects[key]

    def put_bytes(self, key: str, data: bytes, content_type: str | None = None) -> None:
        self.objects[key] = data
        self.puts.append(key)

    def delete_prefix(self, prefix: str) -> int:
        victims = [k for k in self.objects if k.startswith(prefix)]
        for k in victims:
            del self.objects[k]
        self.deletes.extend(victims)
        return len(victims)

    def json(self, key: str) -> dict:
        return json.loads(self.objects[key])


class FakeDb:
    """Implements exactly the surface docpulse.embed uses."""

    def __init__(self, lock_available: bool = True) -> None:
        self.rows: dict[str, tuple[int, str, str, list[float], int]] = {}
        self.models: dict[str, dict] = {}
        self.runs: dict[str, dict] = {}
        self.batches: list[int] = []
        self.lock_available = lock_available
        self.schema_dims: list[int] = []

    # -- lock ------------------------------------------------------------
    @contextmanager
    def advisory_lock(self):
        yield self.lock_available

    def ensure_schema(self, dim: int) -> dict:
        self.schema_dims.append(dim)
        return {"table": "paper_embeddings", "dim": dim}

    # -- models ----------------------------------------------------------
    def ensure_model(self, info: dict) -> None:
        self.models.setdefault(info["fingerprint"], dict(info))

    def model_fingerprints(self) -> dict[str, dict]:
        return dict(self.models)

    # -- state -----------------------------------------------------------
    def fetch_state(self) -> dict[str, tuple[str, str]]:
        return {pid: (v[1], v[2]) for pid, v in self.rows.items()}

    def ids_for_model(self, model_fp: str) -> set[str]:
        return {pid for pid, v in self.rows.items() if v[2] == model_fp}

    def text_hashes(self) -> dict[str, str]:
        return {pid: v[1] for pid, v in self.rows.items()}

    # -- writes ----------------------------------------------------------
    def upsert_batch(self, rows) -> int:
        for pid, version, text_hash, fp, vec, chars in rows:
            self.rows[pid] = (version, text_hash, fp, vec, chars)
        self.batches.append(len(rows))
        return len(rows)

    def delete_model_rows(self, model_fp: str) -> int:
        victims = [pid for pid, v in self.rows.items() if v[2] == model_fp]
        for pid in victims:
            del self.rows[pid]
        return len(victims)

    def delete_ids(self, ids) -> int:
        n = 0
        for pid in ids:
            if pid in self.rows:
                del self.rows[pid]
                n += 1
        return n

    # -- ledger ----------------------------------------------------------
    def start_run(self, run_id, model_fp, n_planned, dry_run) -> None:
        self.runs[run_id] = {
            "run_id": run_id,
            "model_fp": model_fp,
            "n_planned": n_planned,
            "dry_run": dry_run,
            "status": "running",
        }

    def finish_run(self, run_id, status, counters, notes=None) -> None:
        self.runs[run_id].update(dict(counters), status=status, notes=notes or {})

    def recent_runs(self, n=10):
        return list(self.runs.values())[-n:]

    # -- stats -----------------------------------------------------------
    def stats(self, model_fp: str) -> dict:
        norms = []
        dims = set()
        for _version, _text_hash, _fp, vec, _chars in self.rows.values():
            norms.append(math.sqrt(sum(float(x) ** 2 for x in vec)))
            dims.add(len(vec))
        expected = self.models.get(model_fp, {}).get("dim")
        return {
            "n_rows": len(self.rows),
            "n_null": sum(1 for v in self.rows.values() if v[3] is None),
            "min_norm": min(norms) if norms else None,
            "max_norm": max(norms) if norms else None,
            "n_distinct_hashes": len({v[1] for v in self.rows.values()}),
            "n_model_fps": len({v[2] for v in self.rows.values()}),
            "chars_total": sum(v[4] for v in self.rows.values()),
            "model_fps": sorted({v[2] for v in self.rows.values()}),
            "n_wrong_dim": sum(1 for d in dims if expected is not None and d != expected),
        }


class FlakyEncoder:
    """Wraps a real encoder and fails on the Nth encode() call."""

    def __init__(self, inner, fail_on_call: int) -> None:
        self.inner = inner
        self.fail_on_call = fail_on_call
        self.calls = 0

    # identity passthrough
    dim = property(lambda self: self.inner.dim)  # type: ignore[assignment]
    normalize = property(lambda self: self.inner.normalize)  # type: ignore[assignment]
    model_id = property(lambda self: self.inner.model_id)  # type: ignore[assignment]
    model_revision = property(lambda self: self.inner.model_revision)  # type: ignore[assignment]
    fingerprint = property(lambda self: self.inner.fingerprint)  # type: ignore[assignment]

    def info(self) -> dict:
        return self.inner.info()

    def encode(self, texts):
        self.calls += 1
        if self.calls == self.fail_on_call:
            raise RuntimeError("simulated worker death mid-run")
        return self.inner.encode(texts)


def make_silver_rows(n: int = 5, *, prefix: str = "2601.0000") -> list[dict]:
    rows = []
    for i in range(n):
        rows.append(
            {
                "paper_id": f"{prefix}{i}",
                "version": 1,
                "text_hash": f"hash-{i}",
                "title": f"Paper {i}",
                "abstract": f"Abstract body for paper {i}.",
            }
        )
    return rows


def make_snapshot(rows: list[dict] | None = None, *, months: int = 2) -> SilverSnapshot:
    rows = make_silver_rows() if rows is None else rows
    manifest = {
        "n_papers_total": len(rows),
        "contract_version": 1,
        "months": [{"month": f"2026-0{i + 1}", "rows": len(rows)} for i in range(months)],
    }
    return SilverSnapshot(
        manifest_key=str(SILVER_MANIFEST_KEY),
        manifest=manifest,
        rows=rows,
        decode_mode="fake",
    )


def loader(snapshot: SilverSnapshot):
    return lambda store: snapshot


def seed_silver_store(rows: list[dict] | None = None, *, with_text_hash: bool = True) -> FakeStore:
    """Build a FakeStore holding a JSON stand-in for the silver layer."""
    rows = make_silver_rows() if rows is None else rows
    payload = []
    for r in rows:
        item = {
            "arxiv_id": r["paper_id"],  # deliberately use an alias column name
            "version": r["version"],
            "title": r["title"],
            "abstract": r["abstract"],
        }
        if with_text_hash and r.get("text_hash"):
            item["text_hash"] = r["text_hash"]
        payload.append(item)
    month_key = "silver/papers/paper_month=2026-01/part-00000.json"
    manifest = {
        "n_papers_total": len(rows),
        "contract_version": 1,
        "months": [{"month": "2026-01", "rows": len(rows), "key": month_key}],
    }
    store = FakeStore(
        {
            str(SILVER_MANIFEST_KEY): json.dumps(manifest).encode(),
            month_key: json.dumps(payload).encode(),
        }
    )
    return store
