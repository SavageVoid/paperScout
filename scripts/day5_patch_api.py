#!/usr/bin/env python3
"""Day 5 step 3d: teach /search about mode, sort and min_score.

Four anchored replacements in the /search handler, in this order:

  1. three query parameters, plus a local import of the policy helpers
  2. the branch: keyword arm or vector arm, instead of always vector
  3. two response fields that say which arm ran and which order was used
  4. the score floor, echoed back so a filtered response is never silent

Anchor on text that appears exactly once; refuse to write anything if an anchor has
drifted. Re-running is a no-op. --dry-run prints a unified diff first.

    python scripts/day5_patch_api.py --dry-run
    python scripts/day5_patch_api.py
"""

from __future__ import annotations

import argparse
import ast
import difflib
import pathlib
import sys

TARGET = pathlib.Path("src/docpulse/search/api.py")
APPLIED_MARKER = 'mode: str = Query("vector"'

PARAMS_OLD = '''        include_abstract: bool = Query(False),
    ) -> dict[str, Any]:'''

PARAMS_NEW = '''        include_abstract: bool = Query(False),
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
'''

BODY_OLD = '''        started = time.perf_counter()
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
'''

BODY_NEW = '''        if mode not in MODES:
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

        if mode == "keyword":
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
                vector = query_encoder.encode(text)
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
'''

ECHO_OLD = '''        results = rows_to_results(rows, include_abstract=include_abstract)
        return {
            "query": text,
            "model": {'''

ECHO_NEW = '''        results = rows_to_results(rows, include_abstract=include_abstract)
        return {
            "query": text,
            "mode": mode,
            "sort": sort,
            "model": {'''

FLOOR_OLD = '''            "filters": {"category": category, "since": since, "until": until},
            "n_returned": len(results),'''

FLOOR_NEW = '''            "filters": {"category": category, "since": since, "until": until},
            "min_score": min_score,
            "keyword": keyword_info,
            "n_returned": len(results),'''

EDITS = (
    ("query parameters", PARAMS_OLD, PARAMS_NEW),
    ("mode branch", BODY_OLD, BODY_NEW),
    ("mode and sort echoed back", ECHO_OLD, ECHO_NEW),
    ("floor and keyword diagnostics echoed back", FLOOR_OLD, FLOOR_NEW),
)


def patch(text: str) -> tuple[str, list[str]]:
    if APPLIED_MARKER in text:
        return text, ["already applied; nothing to do"]

    for name, anchor, _new in EDITS:
        hits = text.count(anchor)
        if hits != 1:
            raise SystemExit(
                f"ERROR: anchor for {name!r} appears {hits} times, expected exactly 1.\n"
                "Refusing to guess. Nothing was written."
            )

    done = []
    for name, anchor, new in EDITS:
        text = text.replace(anchor, new, 1)
        done.append(name)
    return text, done


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Add mode/sort/min_score to /search")
    parser.add_argument("--dry-run", action="store_true", help="print a diff, write nothing")
    parser.add_argument("--path", default=str(TARGET), help="target file")
    args = parser.parse_args(argv)

    path = pathlib.Path(args.path)
    if not path.is_file():
        print(f"ERROR: {path} not found (run from the repo root)")
        return 2

    before = path.read_text()
    after, done = patch(before)
    for name in done:
        print(f"  {name}")
    if done[0].startswith("already"):
        return 0

    ast.parse(after)

    if args.dry_run:
        sys.stdout.writelines(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile=f"{path} (current)",
                tofile=f"{path} (patched)",
                n=2,
            )
        )
        print("--dry-run: nothing written.")
        return 0

    path.write_text(after)
    print(f"  wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
