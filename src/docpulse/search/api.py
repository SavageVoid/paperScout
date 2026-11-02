"""Day 4: the FastAPI application.

Route handlers are synchronous ``def`` on purpose: FastAPI runs them in a
threadpool, which keeps the blocking psycopg call and the CPU-bound encode off
the event loop without an async database driver.

``create_app`` takes optional overrides so tests can inject a fake database and a
hash encoder — no Postgres, no torch, no network.
"""

from __future__ import annotations

import time
from typing import Any

from .config import MAX_QUERY_CHARS, MIN_QUERY_CHARS, SearchSettings
from .db import rows_to_results
from .page import INDEX_HTML
from .query import QueryEncoder, normalise_query

DEFAULT_ABSTRACT_RESULTS = 3


def create_app(
    *,
    settings: SearchSettings | None = None,
    db: Any = None,
    encoder: Any = None,
    query_encoder: QueryEncoder | None = None,
):
    from contextlib import asynccontextmanager

    from fastapi import FastAPI, HTTPException, Query
    from fastapi.responses import HTMLResponse, JSONResponse

    settings = settings or SearchSettings.from_env()

    if encoder is None:  # pragma: no cover - needs torch
        from ..embed.cli import build_encoder

        encoder = build_encoder(settings.embedding)
    query_encoder = query_encoder or QueryEncoder(encoder, cache_size=settings.cache_size)

    if db is None:  # pragma: no cover - needs postgres
        from .db import PgSearchDb

        db = PgSearchDb(settings.dsn, table=settings.table, meta_table=settings.meta_table)

    @asynccontextmanager
    async def lifespan(_app):  # pragma: no cover - exercised by running the service
        try:
            db.connect()
        except Exception:
            pass  # do not refuse to boot: /health is how you find out what is wrong
        try:
            yield
        finally:
            try:
                db.close()
            except Exception:
                pass

    app = FastAPI(
        title="DocPulse search",
        version="0.1.0",
        description="Semantic search over the Day 3 embedding table.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.db = db
    app.state.query_encoder = query_encoder
    app.state.started_at = time.time()

    # -- routes ---------------------------------------------------------
    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index() -> str:
        return INDEX_HTML

    @app.get("/health")
    def health() -> JSONResponse:
        report = db.health(encoder.fingerprint)
        report["model"] = encoder.info() if hasattr(encoder, "info") else {}
        report["query_cache"] = query_encoder.stats()
        report["uptime_s"] = round(time.time() - app.state.started_at, 1)
        ok = (
            report.get("db") == "up"
            and int(report.get("n_vectors") or 0) > 0
            and bool(report.get("model_matches"))
            and bool(report.get("metadata_loaded"))
        )
        report["ok"] = ok
        if not ok:
            report["hint"] = _health_hint(report)
        return JSONResponse(report, status_code=200 if ok else 503)

    @app.get("/search")
    def search(
        q: str = Query(..., min_length=MIN_QUERY_CHARS, max_length=MAX_QUERY_CHARS),
        limit: int | None = Query(None, ge=1),
        category: str | None = Query(None, max_length=64),
        since: str | None = Query(None, max_length=32, description="YYYY-MM-DD"),
        until: str | None = Query(None, max_length=32, description="YYYY-MM-DD"),
        include_abstract: bool = Query(False),
    ) -> dict[str, Any]:
        text = normalise_query(q)
        if not text:
            raise HTTPException(status_code=422, detail="query is empty after trimming")

        started = time.perf_counter()
        try:
            vector = query_encoder.encode(text)
        except Exception as exc:  # pragma: no cover - model failure
            raise HTTPException(status_code=503, detail=f"encoder failed: {exc}") from exc

        clamped = settings.clamp_limit(limit)
        try:
            rows = db.search(
                vector=vector,
                model_fp=encoder.fingerprint,
                limit=clamped,
                category=category,
                since=since,
                until=until,
            )
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"database unavailable: {exc}") from exc

        results = rows_to_results(rows, include_abstract=include_abstract)
        return {
            "query": text,
            "model": {
                "id": settings.model_id,
                "revision": settings.model_revision,
                "fingerprint": encoder.fingerprint,
                "dim": settings.dim,
            },
            "limit": clamped,
            "limit_requested": limit,
            "filters": {"category": category, "since": since, "until": until},
            "n_returned": len(results),
            "took_ms": round((time.perf_counter() - started) * 1000, 1),
            "results": results,
        }

    @app.get("/papers/{arxiv_id}")
    def paper(arxiv_id: str) -> dict[str, Any]:
        row = db.paper(arxiv_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"no paper {arxiv_id!r}")
        return row

    @app.get("/stats")
    def stats() -> dict[str, Any]:
        try:
            out = db.stats(encoder.fingerprint)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"database unavailable: {exc}") from exc
        out["query_cache"] = query_encoder.stats()
        out["settings"] = settings.public()
        return out

    return app


def _health_hint(report: dict[str, Any]) -> str:
    if report.get("db") != "up":
        return "database unreachable — is the stack up? check SEARCH dsn / POSTGRES_PASSWORD"
    if int(report.get("n_vectors") or 0) == 0:
        return "no vectors yet — run `make -f day3.mk embed`"
    if not report.get("metadata_loaded"):
        return "paper_meta is empty — run `make -f day4.mk search-meta`"
    if not report.get("model_matches"):
        return (
            "the table holds a different model fingerprint than this service encodes with "
            f"({report.get('model_fps')}) — rebuild vectors or fix EMBED_MODEL_ID"
        )
    return ""
