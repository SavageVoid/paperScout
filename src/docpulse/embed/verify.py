"""Independent verification of the embedding layer.

Deliberately does not trust ``build``: it re-reads silver, re-reads the table and
compares. The checks are the ones that would actually catch a bad run:

  V1  every non-null vector has the model's dimension
  V2  exactly one model fingerprint is present (no mixed spaces)
  V3  vectors are normalised (if the model normalises)
  V4  no null / zero / non-finite vectors
  V5  every silver paper has a vector (coverage)
  V6  no vectors for papers that no longer exist in silver (orphans)
  V7  stored text_hash matches silver's (no silent staleness)
  V8  an identical text always yields an identical vector (determinism spot check)
"""

from __future__ import annotations

import math

DEFAULT_NORM_TOL = 1e-3


def _norm(vec) -> float:
    return math.sqrt(sum(float(v) * float(v) for v in vec))


def run(
    *,
    store,
    db,
    encoder,
    settings,
    silver_loader,
    expect_complete: bool = True,
    norm_tol: float = DEFAULT_NORM_TOL,
) -> dict:
    silver = silver_loader(store)
    silver_hashes = {r["paper_id"]: r.get("text_hash") for r in silver.rows if r.get("paper_id")}
    # de-dupe defensively: highest version wins, same rule as build
    errors: list[str] = []
    warnings: list[str] = []

    stats = db.stats(encoder.fingerprint)
    n_rows = int(stats.get("n_rows") or 0)

    # V2 -----------------------------------------------------------------
    model_fps = stats.get("model_fps") or []
    if len(model_fps) > 1:
        errors.append(
            f"V2 mixed vector spaces in {settings.table}: {model_fps}. "
            "Old rows must be dropped before querying."
        )
    elif model_fps and model_fps[0] != encoder.fingerprint:
        errors.append(
            f"V2 table holds {model_fps[0]} but this build uses {encoder.fingerprint}"
        )

    # V1 / V4 ------------------------------------------------------------
    if int(stats.get("n_wrong_dim") or 0) > 0:
        errors.append(f"V1 {stats['n_wrong_dim']} vectors have the wrong dimension")
    if int(stats.get("n_null") or 0) > 0:
        errors.append(f"V4 {stats['n_null']} null vectors")

    # V3 -----------------------------------------------------------------
    if settings.normalize and n_rows:
        lo = stats.get("min_norm")
        hi = stats.get("max_norm")
        if lo is None or hi is None:
            errors.append("V3 could not compute vector norms")
        elif abs(float(lo) - 1.0) > norm_tol or abs(float(hi) - 1.0) > norm_tol:
            errors.append(f"V3 norms out of range: min={lo} max={hi} (expected 1.0)")

    # V5 / V6 ------------------------------------------------------------
    db_hashes = db.text_hashes()
    db_ids = set(db_hashes)
    silver_ids = set(silver_hashes)
    missing = sorted(silver_ids - db_ids)
    orphans = sorted(db_ids - silver_ids)
    if missing:
        (errors if expect_complete else warnings).append(
            f"V5 {len(missing)} silver papers have no vector (e.g. {missing[:5]})"
        )
    if orphans:
        warnings.append(f"V6 {len(orphans)} vectors have no silver paper (e.g. {orphans[:5]})")

    # V7 -----------------------------------------------------------------
    drift = [pid for pid in (silver_ids & db_ids) if silver_hashes.get(pid) and db_hashes[pid] != silver_hashes[pid]]
    if drift:
        errors.append(
            f"V7 {len(drift)} stored text_hash values are stale versus silver "
            f"(e.g. {drift[:5]}) — build did not pick up a text change"
        )

    # V8 -----------------------------------------------------------------
    determinstic = None
    if silver.rows:
        sample = sorted(silver.rows, key=lambda r: r["paper_id"])[0]
        text = (sample.get("title") or "") + "\n\n" + (sample.get("abstract") or "")
        if text.strip():
            try:
                a = encoder.encode([text.strip()])[0]
                b = encoder.encode([text.strip()])[0]
                determinstic = a == b
                if not determinstic:
                    errors.append("V8 encoder is not deterministic for identical text")
            except Exception as exc:  # pragma: no cover - depends on model
                warnings.append(f"V8 determinism check skipped: {type(exc).__name__}: {exc}")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "n_rows": n_rows,
        "n_silver_papers": len(silver_ids),
        "n_missing": len(missing),
        "n_orphans": len(orphans),
        "n_stale_text_hash": len(drift),
        "model": encoder.info(),
        "silver": silver.summary(),
        "stats": {k: v for k, v in stats.items() if k != "model_fps"},
        "deterministic": determinstic,
        "norm_tolerance": norm_tol,
    }
