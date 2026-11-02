#!/usr/bin/env python3
"""Day 3 audit: what does silver's ``text_hash`` actually cover?  (item B4)

Read-only. Day 3 decides "changed" by comparing ``text_hash``. If that hash
ignores a field that feeds the embedding (the title), a revision is invisible and
the stored vector keeps describing the old text.

This recovers the recipe from the data instead of reading build.py, so it reports
what actually ran:

    make -f day3.mk embed-audit-hash
    python scripts/day3_hash_audit.py --json --sample 50
"""

from __future__ import annotations

import argparse
import json
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Recover silver's text_hash recipe (B4)")
    parser.add_argument("--sample", type=int, default=150, help="rows used for the cheap screen")
    parser.add_argument("--limit", type=int, default=0, help="only the first N month files (0 = all)")
    parser.add_argument("--json", action="store_true", help="machine-readable report")
    args = parser.parse_args(argv)

    from docpulse.embed.compat import _decode_month, find_silver_manifest_key, get_store
    from docpulse.embed.hashaudit import identify

    store = get_store()
    manifest_key = find_silver_manifest_key(store)
    manifest = json.loads(store.get_bytes(manifest_key))
    months = manifest.get("months") or manifest.get("parts") or manifest.get("files") or []
    if args.limit:
        months = months[: args.limit]

    rows: list[dict] = []
    for item in months:
        mkey = item if isinstance(item, str) else (item.get("key") or item.get("path"))
        if not mkey:
            continue
        decoded, _mode = _decode_month(store, mkey)
        rows.extend(decoded)

    if not args.json:
        print(f"manifest      {manifest_key}")
        print(f"rows read     {len(rows)} from {len(months)} month file(s)")
        print("")

    report = identify(rows, sample=args.sample)

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"hash length   {report['hash_length']}")
        print(f"usable rows   {report['n_rows_usable']} of {report['n_rows_seen']}")
        print(f"candidates    {report['n_candidates_tried']} recipes tried")
        print(f"verdict       {report['verdict']}")
        if report["verdict"] == "identified":
            print(f"recipe        {report['recipe']}")
            print(f"  covers title       {report['covers_title']}")
            print(f"  covers categories  {report['covers_categories']}")
            print(f"  matching recipes   {report['n_matching_recipes']} (ambiguous if > 1)")
        else:
            print(f"  {report.get('hint', '')}")
        if report["n_identical_hashes_different_text"]:
            print(
                "collisions    "
                f"{report['n_identical_hashes_different_text']} rows share a hash with different text"
            )
        if report.get("warning"):
            print("")
            print("!" * 72)
            print(report["warning"])
            print("!" * 72)

    # The audit is diagnostic: a finding is not a pipeline failure, so exit 0.
    return 0


if __name__ == "__main__":
    sys.exit(main())
