"""Print a one-line fingerprint of a bronze partition (run inside the Airflow container).

    make digest DATE=2026-10-28

Used for the idempotency check: after re-running a day, `objects` must not grow, `fetched_at`
must move forward, and `entries_fp` must stay the same while the arXiv feed itself has not changed.
`page_digest` is shown for reference only: it MAY change between fetches (responses can embed the
time of the request) or may not (identical URLs were once served byte-identical), so it proves nothing.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import date

from docpulse.config import StorageSettings
from docpulse.ingest.bronze import partition_prefix
from docpulse.ingest.fingerprint import entries_fingerprint
from docpulse.storage import S3Store


def digest(store, day: date) -> dict:
    prefix = partition_prefix(day)
    m = json.loads(store.get_bytes(prefix + "_manifest.json"))
    page_digest = hashlib.sha256("".join(p["sha256"] for p in m["pages"]).encode()).hexdigest()[:16]
    entries_fp, n_distinct = entries_fingerprint(store.get_bytes(prefix + p["name"]) for p in m["pages"])
    return {
        "objects": len(store.list_keys(prefix)),
        "pages": m["n_pages"],
        "entries": m["n_entries_total"],
        "in_window": m["n_entries_in_window"],
        "fetched_at": m["fetched_at"],
        "newest_seen": m["freshness"]["newest_updated_seen"],
        "behind_schedule": m["freshness"]["behind_schedule"],
        "distinct_entries": n_distinct,
        "entries_fp": entries_fp,
        "page_digest": page_digest,
    }


if __name__ == "__main__":
    day = date.fromisoformat(sys.argv[1])
    print(" | ".join(f"{k}: {v}" for k, v in digest(S3Store.from_settings(StorageSettings.from_env()), day).items()))
