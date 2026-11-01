"""Build and verify the silver layer from bronze.

Layout::

    silver/papers/published_month=YYYY-MM/papers.<ext>            one row per paper, latest version
    silver/quarantine/ingest_date=YYYY-MM-DD/quarantine.<ext>     rejected entries + reason codes
    silver/_manifest.json                                         what exists, with hashes

Design rules:
  * Bronze is replayable raw data; silver is derived. Every run re-reads the selected bronze
    partitions, so a rerun with the same inputs produces the same output (idempotent), and a
    rebuild from scratch equals the incremental result.
  * Silver is MERGED, not replaced: bronze only holds a few days, so a month file also contains
    papers older than any bronze partition. A paper is replaced only by a newer (version, updated).
  * Bad entries are quarantined with reason codes, never dropped. If too many are bad the build
    fails loudly and writes nothing (circuit breaker), because that means something structural broke.
  * A file is rewritten only if its bytes changed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import date, datetime, timezone
from typing import Any

from defusedxml import ElementTree as SafeET
from defusedxml.common import DefusedXmlException
from xml.etree.ElementTree import ParseError

from docpulse.silver.codecs import Field, TableCodec
from docpulse.silver.contract import Contract, violations
from docpulse.silver.parse import ATOM, entry_to_xml, parse_entry, text_hash
from docpulse.storage import ObjectNotFound, ObjectStore

log = logging.getLogger(__name__)

BRONZE_PREFIX = "bronze/arxiv/"
PAPERS_PREFIX = "silver/papers/"
QUARANTINE_PREFIX = "silver/quarantine/"
MANIFEST_KEY = "silver/_manifest.json"
_BRONZE_KEY_RE = re.compile(r"^bronze/arxiv/ingest_date=(\d{4}-\d{2}-\d{2})/(.+)$")
_MONTH_KEY_RE = re.compile(r"^silver/papers/published_month=(\d{4}-\d{2})/papers\.\w+$")

QUARANTINE_FIELDS = (
    Field("ingest_date", "string"),
    Field("page", "string"),
    Field("position", "int32"),
    Field("arxiv_id_hint", "string"),
    Field("reasons", "list_string"),
    Field("detail", "string"),
    Field("raw_entry", "string"),
)

CodecFactory = Callable[[Sequence[Field]], TableCodec]


class SilverError(RuntimeError):
    pass


class ContractViolation(SilverError):
    """Too much of the input broke the contract (or bronze is unreadable): fail loudly, write nothing."""


class NoBronzeDataError(SilverError):
    pass


class SilverVerificationError(SilverError):
    pass


def paper_fields(contract: Contract) -> list[Field]:
    return [Field(c.name, c.type) for c in contract.columns]


def month_of(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).strftime("%Y-%m")


def papers_key(month: str, codec: TableCodec) -> str:
    return f"{PAPERS_PREFIX}published_month={month}/papers.{codec.extension}"


def quarantine_key(day: date, codec: TableCodec) -> str:
    return f"{QUARANTINE_PREFIX}ingest_date={day.isoformat()}/quarantine.{codec.extension}"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _order_key(row: dict[str, Any]) -> tuple[int, datetime]:
    return (row["version"], row["updated"])


# --------------------------------------------------------------------------- bronze discovery


def discover_bronze(
    store: ObjectStore, since: date | None = None, until: date | None = None
) -> tuple[list[date], list[date]]:
    """(complete partitions, incomplete partitions), ascending, optionally limited to [since, until]."""
    names: dict[date, set[str]] = {}
    for key in store.list_keys(BRONZE_PREFIX):
        m = _BRONZE_KEY_RE.match(key)
        if m:
            names.setdefault(date.fromisoformat(m.group(1)), set()).add(m.group(2))
    days = sorted(d for d in names if (since is None or d >= since) and (until is None or d <= until))
    complete = [d for d in days if "_manifest.json" in names[d]]
    incomplete = [d for d in days if "_manifest.json" not in names[d]]
    return complete, incomplete


# --------------------------------------------------------------------------- scan


class _Scan:
    def __init__(self) -> None:
        self.winners: dict[str, dict[str, Any]] = {}  # arxiv_id -> row; also tracks snapshot order
        self._winner_rank: dict[str, tuple] = {}
        self.quarantine: dict[date, list[dict[str, Any]]] = {}
        self.n_entries = 0
        self.n_valid = 0
        self.n_quarantined = 0
        self.reasons: Counter[str] = Counter()
        self.examples: list[dict[str, Any]] = []

    def accept(self, row: dict[str, Any], day: date) -> None:
        self.n_valid += 1
        rank = (row["version"], row["updated"], day.isoformat())
        prev = self._winner_rank.get(row["arxiv_id"])
        if prev is None or rank >= prev:  # equal rank: the later page/partition wins (iteration order)
            self.winners[row["arxiv_id"]] = row
            self._winner_rank[row["arxiv_id"]] = rank

    def reject(self, day: date, record: dict[str, Any]) -> None:
        self.n_quarantined += 1
        self.quarantine.setdefault(day, []).append(record)
        self.reasons.update(record["reasons"])
        if len(self.examples) < 3:
            self.examples.append({k: record[k] for k in ("ingest_date", "arxiv_id_hint", "reasons", "detail")})


def _scan_partition(store: ObjectStore, day: date, contract: Contract, scan: _Scan) -> None:
    prefix = f"{BRONZE_PREFIX}ingest_date={day.isoformat()}/"
    manifest = json.loads(store.get_bytes(prefix + "_manifest.json"))
    fetched_at = datetime.fromisoformat(manifest["fetched_at"].replace("Z", "+00:00"))
    for page in manifest["pages"]:
        try:
            root = SafeET.fromstring(store.get_bytes(prefix + page["name"]))
        except (ParseError, DefusedXmlException, ObjectNotFound) as exc:
            raise ContractViolation(f"bronze page {prefix}{page['name']} is unreadable: {exc}") from exc
        for position, entry in enumerate(root.findall(ATOM + "entry")):
            scan.n_entries += 1
            parsed = parse_entry(entry, contract.known_elements)
            found = violations(contract, parsed.row, reference_time=fetched_at)
            if parsed.unknown_elements:
                found.append(("schema_drift", "unknown elements: " + ", ".join(parsed.unknown_elements)))
            if not found:
                scan.accept(parsed.row, day)
                continue
            reasons = list(dict.fromkeys(r for r, _ in found))
            detail = "; ".join(d for _, d in found) + ("; " + "; ".join(parsed.notes) if parsed.notes else "")
            scan.reject(
                day,
                {
                    "ingest_date": day.isoformat(),
                    "page": page["name"],
                    "position": position,
                    "arxiv_id_hint": parsed.id_hint,
                    "reasons": reasons,
                    "detail": detail,
                    "raw_entry": entry_to_xml(entry),
                },
            )


# --------------------------------------------------------------------------- build


def build_silver(
    store: ObjectStore,
    contract: Contract,
    codec_factory: CodecFactory,
    *,
    since: date | None = None,
    until: date | None = None,
    dry_run: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    paper_codec = codec_factory(paper_fields(contract))
    quarantine_codec = codec_factory(QUARANTINE_FIELDS)

    complete, incomplete = discover_bronze(store, since, until)
    if incomplete:
        log.warning("skipping %d bronze partition(s) without a manifest: %s", len(incomplete), incomplete)
    if not complete:
        raise NoBronzeDataError("no complete bronze partitions found")

    scan = _Scan()
    for day in complete:
        _scan_partition(store, day, contract, scan)
    if scan.n_entries == 0:
        raise NoBronzeDataError("bronze partitions contain no entries")

    fraction = scan.n_quarantined / scan.n_entries
    if fraction > contract.max_quarantine_fraction:
        raise ContractViolation(
            f"{scan.n_quarantined} of {scan.n_entries} entries ({fraction:.1%}) violate contract "
            f"{contract.name} v{contract.version}, above the {contract.max_quarantine_fraction:.1%} limit. "
            f"Reasons: {dict(scan.reasons)}. Example: {scan.examples[0] if scan.examples else None}. "
            "Nothing was written."
        )

    report: dict[str, Any] = {
        "contract": {"name": contract.name, "version": contract.version, "sha256": contract.sha256},
        "n_bronze_partitions": len(complete),
        "n_bronze_partitions_skipped_incomplete": len(incomplete),
        "n_entries": scan.n_entries,
        "n_valid_entries": scan.n_valid,
        "n_quarantined": scan.n_quarantined,
        "quarantine_by_reason": dict(sorted(scan.reasons.items())),
        "n_unique_papers_in_bronze": len(scan.winners),
        "n_superseded_duplicates": scan.n_valid - len(scan.winners),
        "dry_run": dry_run,
    }
    if dry_run:
        report["quarantine_examples"] = scan.examples
        return report

    # ---- merge winners into month files
    by_month: dict[str, list[dict[str, Any]]] = {}
    for row in scan.winners.values():
        by_month.setdefault(month_of(row[contract.partition_column]), []).append(row)

    n_new = n_revised = n_text_changed = n_stale = 0
    months_written: list[str] = []
    months_unchanged: list[str] = []
    for month in sorted(by_month):
        key = papers_key(month, paper_codec)
        try:
            old_bytes: bytes | None = store.get_bytes(key)
            existing = {r["arxiv_id"]: r for r in paper_codec.decode(old_bytes)}
        except ObjectNotFound:
            old_bytes, existing = None, {}
        merged = dict(existing)
        for row in by_month[month]:
            prev = existing.get(row["arxiv_id"])
            if prev is None:
                n_new += 1
            elif _order_key(row) < _order_key(prev):
                n_stale += 1
                continue
            else:
                if row["version"] > prev["version"]:
                    n_revised += 1
                if row["text_hash"] != prev["text_hash"]:
                    n_text_changed += 1
            merged[row["arxiv_id"]] = row
        rows = [merged[k] for k in sorted(merged)]
        data = paper_codec.encode(rows)
        if old_bytes == data:
            months_unchanged.append(month)
        else:
            store.put_bytes(key, data, "application/octet-stream")
            months_written.append(month)

    # ---- quarantine: rewritten per processed bronze partition, so it always mirrors the current contract
    for day in complete:
        key = quarantine_key(day, quarantine_codec)
        records = scan.quarantine.get(day, [])
        if records:
            data = quarantine_codec.encode(records)
            try:
                if store.get_bytes(key) == data:
                    continue
            except ObjectNotFound:
                pass
            store.put_bytes(key, data, "application/octet-stream")
        else:
            store.delete_prefix(f"{QUARANTINE_PREFIX}ingest_date={day.isoformat()}/")

    manifest = _write_manifest(store, contract, paper_codec, now or datetime.now(timezone.utc))
    report.update(
        {
            "n_new_papers": n_new,
            "n_revised_papers": n_revised,
            "n_text_changed_papers": n_text_changed,
            "n_stale_ignored": n_stale,
            "months_written": months_written,
            "months_unchanged": months_unchanged,
            "n_papers_total": manifest["n_papers_total"],
        }
    )
    return report


def _write_manifest(
    store: ObjectStore, contract: Contract, codec: TableCodec, built_at: datetime
) -> dict[str, Any]:
    months = []
    total = 0
    for key in store.list_keys(PAPERS_PREFIX):
        m = _MONTH_KEY_RE.match(key)
        if not m:
            continue
        data = store.get_bytes(key)
        n = len(codec.decode(data))
        total += n
        months.append({"month": m.group(1), "key": key, "rows": n, "bytes": len(data), "sha256": _sha(data)})
    manifest = {
        "schema_version": 1,
        "contract": {"name": contract.name, "version": contract.version, "sha256": contract.sha256},
        "codec": codec.extension,
        "n_papers_total": total,
        "months": sorted(months, key=lambda r: r["month"]),
        "built_at": built_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    store.put_bytes(MANIFEST_KEY, json.dumps(manifest, indent=2, sort_keys=True).encode(), "application/json")
    return manifest


# --------------------------------------------------------------------------- verify


def verify_silver(store: ObjectStore, contract: Contract, codec_factory: CodecFactory) -> dict[str, Any]:
    """Independent re-check of what is on disk: hashes, contract rules, partition correctness,
    ordering, uniqueness across the whole table, and no stray files."""
    codec = codec_factory(paper_fields(contract))
    try:
        manifest = json.loads(store.get_bytes(MANIFEST_KEY))
    except ObjectNotFound as exc:
        raise SilverVerificationError("no silver manifest: silver was never built") from exc
    if manifest["contract"]["sha256"] != contract.sha256:
        raise SilverVerificationError(
            "the contract changed since silver was last built; rebuild silver before verifying"
        )
    if manifest["codec"] != codec.extension:
        raise SilverVerificationError(f"silver was written with codec {manifest['codec']!r}, not {codec.extension!r}")

    seen: dict[str, str] = {}
    total = 0
    expected_keys = set()
    for entry in manifest["months"]:
        key = entry["key"]
        expected_keys.add(key)
        try:
            data = store.get_bytes(key)
        except ObjectNotFound as exc:
            raise SilverVerificationError(f"missing file {key}") from exc
        if len(data) != entry["bytes"] or _sha(data) != entry["sha256"]:
            raise SilverVerificationError(f"checksum/size mismatch for {key}")
        rows = codec.decode(data)
        if len(rows) != entry["rows"]:
            raise SilverVerificationError(f"row count mismatch for {key}")
        ids = [r["arxiv_id"] for r in rows]
        if ids != sorted(ids) or len(set(ids)) != len(ids):
            raise SilverVerificationError(f"{key}: ids are not unique and sorted")
        for row in rows:
            bad = violations(contract, row)
            if bad:
                raise SilverVerificationError(f"{key}: {row['arxiv_id']} violates the contract: {bad[0]}")
            if month_of(row[contract.partition_column]) != entry["month"]:
                raise SilverVerificationError(f"{key}: {row['arxiv_id']} is in the wrong month partition")
            if row["text_hash"] != text_hash(row["title"], row["abstract"]):
                raise SilverVerificationError(f"{key}: {row['arxiv_id']} text_hash does not match its text")
            if row["arxiv_id"] in seen:
                raise SilverVerificationError(
                    f"{row['arxiv_id']} appears in both {seen[row['arxiv_id']]} and {entry['month']}"
                )
            seen[row["arxiv_id"]] = entry["month"]
        total += len(rows)

    actual = {k for k in store.list_keys(PAPERS_PREFIX) if _MONTH_KEY_RE.match(k)}
    if actual != expected_keys:
        raise SilverVerificationError(f"files differ from manifest: {sorted(actual ^ expected_keys)[:3]}")
    if total != manifest["n_papers_total"]:
        raise SilverVerificationError("total row count differs from manifest")
    return {"n_months": len(manifest["months"]), "n_papers_total": total, "contract_version": contract.version}
