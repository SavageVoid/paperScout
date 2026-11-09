"""Day 4: the FastAPI application.

Route handlers are synchronous ``def`` on purpose: FastAPI runs them in a
threadpool, which keeps the blocking psycopg call and the CPU-bound encode off
the event loop without an async database driver.

``create_app`` takes optional overrides so tests can inject a fake database and a
hash encoder — no Postgres, no torch, no network.
"""

from __future__ import annotations

import os
import time
from typing import Any

from .config import MAX_QUERY_CHARS, MIN_QUERY_CHARS, SearchSettings
from .db import rows_to_results
from .page import INDEX_HTML
from .query import QueryEncoder, normalise_query

# bge-style models are trained with an instruction on the *query* side only; documents
# must never receive it. Read once at import so it cannot drift mid-process.
QUERY_PREFIX = (os.environ.get("EMBED_QUERY_PREFIX") or "").strip()
if QUERY_PREFIX and not QUERY_PREFIX.endswith(" "):
    QUERY_PREFIX += " "

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
    def health():
        report = db.health(encoder.fingerprint)
        report["model"] = encoder.info() if hasattr(encoder, "info") else {}
        report["query_cache"] = query_encoder.stats()
        report["query_prefix"] = QUERY_PREFIX or None
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
        q: str | None = Query(None, min_length=MIN_QUERY_CHARS, max_length=MAX_QUERY_CHARS),
        author: str | None = Query(None, max_length=200, description="author name"),
        limit: int | None = Query(None, ge=1),
        category: str | None = Query(None, max_length=64),
        since: str | None = Query(None, max_length=32, description="YYYY-MM-DD"),
        until: str | None = Query(None, max_length=32, description="YYYY-MM-DD"),
        include_abstract: bool = Query(False),
        mode: str = Query("vector", description="vector | keyword"),
        sort: str = Query("relevance", description="relevance | updated"),
        min_score: float | None = Query(
            None, ge=-1.0, le=1.0, description="cosine floor, vector mode only"
        ),
    ) -> dict[str, Any]:
        # Imported here so this patch touches one region of the file instead of
        # also editing an import block it has never seen.
        from docpulse.search.modes import (
            MODES,
            SORTS,
            apply_min_score,
            candidate_pool,
            order_by_updated,
        )

        text = normalise_query(q or "")
        wanted_author = " ".join((author or "").split())
        if not text and not wanted_author:
            # Neither was given, so there is nothing to search. A blank q with a
            # real author is fine: the author arm never looks at q.
            raise HTTPException(
                status_code=422, detail="q or author is required (q cannot be blank)"
            )

        if mode not in MODES:
            raise HTTPException(
                status_code=422, detail=f"mode must be one of {', '.join(MODES)}"
            )
        if sort not in SORTS:
            raise HTTPException(
                status_code=422, detail=f"sort must be one of {', '.join(SORTS)}"
            )

        started = time.perf_counter()
        clamped = settings.clamp_limit(limit)
        keyword_info: dict[str, Any] | None = None

        author_info: dict[str, Any] | None = None

        if wanted_author:
            # A separate arm: "this person is in the author list" is a stronger
            # claim than "these words appear", and the two must not be blended.
            try:
                found = db.search_author(
                    author=wanted_author,
                    limit=clamped,
                    category=category,
                    since=since,
                    until=until,
                )
            except Exception as exc:
                raise HTTPException(
                    status_code=503, detail=f"database unavailable: {exc}"
                ) from exc
            rows = found["rows"]
            mode = "author"
            author_info = {
                "mode": found["mode"],
                "key": found["key"],
                "total_matches": found["total_matches"],
            }
        elif mode == "keyword":
            # No encoder call: this arm is full text, and asking the model for a
            # vector only to ignore it would add latency and a failure mode.
            try:
                rows, keyword_info = db.search_keyword(
                    query=text,
                    limit=clamped,
                    category=category,
                    since=since,
                    until=until,
                    sort=sort,
                )
            except Exception as exc:
                raise HTTPException(
                    status_code=503, detail=f"database unavailable: {exc}"
                ) from exc
        else:
            try:
                vector = query_encoder.encode(f"{QUERY_PREFIX}{text}")
            except Exception as exc:  # pragma: no cover - model failure
                raise HTTPException(
                    status_code=503, detail=f"encoder failed: {exc}"
                ) from exc
            # sort=updated re-orders the most similar rows; it does not scan the
            # corpus by date, which would ignore the query entirely.
            pool = clamped if sort == "relevance" else candidate_pool(clamped)
            try:
                rows = db.search(
                    vector=vector,
                    model_fp=encoder.fingerprint,
                    limit=pool,
                    category=category,
                    since=since,
                    until=until,
                )
            except Exception as exc:
                raise HTTPException(
                    status_code=503, detail=f"database unavailable: {exc}"
                ) from exc
            if sort == "updated":
                rows = order_by_updated(rows, clamped)
            # A cosine floor means nothing to a ts_rank_cd score, so it is applied
            # to the vector arm only.
            rows = apply_min_score(rows, min_score)[:clamped]

        results = rows_to_results(rows, include_abstract=include_abstract)
        return {
            "query": text,
            "mode": mode,
            "sort": sort,
            "model": {
                "id": settings.model_id,
                "revision": settings.model_revision,
                "fingerprint": encoder.fingerprint,
                "dim": settings.dim,
            },
            "limit": clamped,
            "limit_requested": limit,
            "filters": {"category": category, "since": since, "until": until},
            "min_score": min_score,
            "keyword": keyword_info,
            "author": author_info,
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
