"""The incremental embedding build.

Crash-safety model
------------------
1. Take the advisory lock (try-lock: a second concurrent run exits immediately).
2. Refuse a model change unless asked, so one column never holds two spaces.
3. Read silver, plan against the database state.
4. Embed and commit **one batch at a time**.
5. Write the manifest last: the manifest is the "this layer is complete" flag.

Kill it anywhere and the next run picks up from what is committed in Postgres,
because planning is derived from the table, not from memory or from a temp file.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone

from .plan import Plan, build_plan


class ModelChangeError(RuntimeError):
    """The table already holds vectors from a different model/revision/dim."""


class LockBusy(RuntimeError):
    """Another embedding run holds the advisory lock."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def run(
    *,
    store,
    db,
    encoder,
    settings,
    silver_loader,
    dry_run: bool = False,
    limit: int | None = None,
    rebuild: bool = False,
    run_id: str | None = None,
) -> dict:
    """silver_loader(store) -> SilverSnapshot (injectable for tests)."""
    started = time.monotonic()
    run_id = run_id or f"embed-{uuid.uuid4().hex[:12]}"
    summary: dict = {
        "run_id": run_id,
        "started_at": _now(),
        "dry_run": dry_run,
        "model": encoder.info(),
        "table": settings.table,
        "manifest_key": settings.manifest_key,
        "warnings": [],
    }

    with db.advisory_lock() as got:
        if not got:
            raise LockBusy(
                "another embedding run holds the advisory lock; wait for `make embed-status` "
                "to show it finished, then re-run"
            )

        # ---- model guard ------------------------------------------------
        known = db.model_fingerprints()
        others = {fp for fp in known if fp != encoder.fingerprint}
        if others and not (rebuild or settings.allow_model_change):
            raise ModelChangeError(
                f"table {settings.table!r} already holds vectors from "
                f"{sorted(others)} but this run uses {encoder.fingerprint}. "
                "Mixing vector spaces silently breaks every similarity query. "
                "Use `make embed-rebuild` (drops and re-embeds this model) or set "
                "EMBED_ALLOW_MODEL_CHANGE=1 if you really mean it."
            )
        if others and not dry_run:
            summary["warnings"].append(f"model change accepted; old spaces dropped: {sorted(others)}")

        # ---- silver -----------------------------------------------------
        silver = silver_loader(store)
        summary["silver"] = silver.summary()

        # ---- plan -------------------------------------------------------
        if rebuild or (others and settings.allow_model_change):
            existing: dict[str, tuple[str, str]] = {}
        else:
            try:
                existing = db.fetch_state()
            except Exception as exc:
                # A missing table on a dry run is expected before the first build.
                if dry_run:
                    existing = {}
                    summary["warnings"].append(f"could not read existing state ({type(exc).__name__}); assuming empty")
                else:
                    raise
        plan: Plan = build_plan(
            silver.rows, existing, encoder.fingerprint, max_chars=settings.max_chars
        )
        summary["plan"] = plan.summary()

        targets = plan.targets
        if limit:
            targets = targets[:limit]
            summary["limit"] = limit
        summary["n_targets"] = len(targets)
        summary["chars_targeted"] = sum(t.chars for t in targets)

        if dry_run:
            summary["status"] = "dry_run"
            summary["seconds"] = round(time.monotonic() - started, 3)
            return summary

        # ---- commit -----------------------------------------------------
        db.ensure_model(encoder.info())
        if rebuild:
            dropped = 0
            for fp in (list(others) + [encoder.fingerprint]):
                dropped += db.delete_model_rows(fp)
            summary["n_dropped_for_rebuild"] = dropped
        elif others and settings.allow_model_change:
            dropped = sum(db.delete_model_rows(fp) for fp in others)
            summary["n_dropped_for_model_change"] = dropped

        db.start_run(run_id, encoder.fingerprint, len(targets), dry_run=False)

        counters = {"n_embedded": 0, "n_skipped": plan.n_unchanged, "n_failed": 0, "chars_total": 0}
        batch_size = settings.batch_size
        try:
            for offset in range(0, len(targets), batch_size):
                batch = targets[offset : offset + batch_size]
                vectors = encoder.encode([t.text for t in batch])
                if len(vectors) != len(batch):
                    raise RuntimeError(
                        f"encoder returned {len(vectors)} vectors for {len(batch)} texts"
                    )
                rows = [
                    (t.paper_id, t.version, t.text_hash, encoder.fingerprint, vec, t.chars)
                    for t, vec in zip(batch, vectors)
                ]
                db.upsert_batch(rows)  # <- the transaction boundary
                counters["n_embedded"] += len(rows)
                counters["chars_total"] += sum(t.chars for t in batch)
        except Exception as exc:
            counters["n_failed"] = len(targets) - counters["n_embedded"]
            counters["seconds"] = round(time.monotonic() - started, 3)
            db.finish_run(
                run_id, "failed", counters, {"error": f"{type(exc).__name__}: {exc}"}
            )
            summary.update(counters)
            summary["status"] = "failed"
            summary["error"] = f"{type(exc).__name__}: {exc}"
            summary["seconds"] = counters["seconds"]
            raise

        # ---- orphans ----------------------------------------------------
        if settings.prune_orphans:
            silver_ids = {t.paper_id for t in plan.targets} | set()
            silver_ids |= {r["paper_id"] for r in silver.rows if r.get("paper_id")}
            orphan_ids = sorted(db.ids_for_model(encoder.fingerprint) - silver_ids)
            summary["n_orphans_pruned"] = db.delete_ids(orphan_ids)
        else:
            summary["n_orphans_pruned"] = 0

        # ---- ledger + manifest -----------------------------------------
        counters["seconds"] = round(time.monotonic() - started, 3)
        summary.update(counters)
        summary["status"] = "success"
        summary["finished_at"] = _now()
        summary["seconds"] = counters["seconds"]

        db.finish_run(run_id, "success", counters, {"plan": plan.summary()})
        store.put_bytes(
            settings.manifest_key,
            json.dumps(summary, indent=2, sort_keys=True, default=str).encode("utf-8"),
            "application/json",
        )
        return summary
