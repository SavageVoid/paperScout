"""Load a YAML data contract and validate rows against it.

The contract drives everything: which columns exist, their types, which values are allowed, and
which quarantine reason code a violation maps to. The validator is plain Python (no pandas), so
it runs anywhere and is trivially testable.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

TYPES = frozenset({"string", "int32", "list_string", "timestamp_utc"})
_KNOWN_COLUMN_KEYS = {"name", "type", "nullable", "reason", "pattern", "min_length", "min_items", "min_value", "derived_from"}


class ContractError(ValueError):
    """The contract file itself is malformed."""


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    nullable: bool = False
    reason: str | None = None
    pattern: re.Pattern[str] | None = None
    min_length: int = 1
    min_items: int = 1
    min_value: int | None = None
    derived_from: tuple[str, ...] = ()  # computed from these columns; silent if they are already flagged


@dataclass(frozen=True)
class Contract:
    name: str
    version: int
    sha256: str
    columns: tuple[Column, ...]
    primary_key: str
    partition_column: str
    known_elements: frozenset[str]
    reasons: dict[str, str]
    max_quarantine_fraction: float
    min_published: datetime
    max_future_skew_hours: float

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns)


def _parse_ts(value: Any) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def load_contract(path: str | Path) -> Contract:
    raw = Path(path).read_bytes()
    doc = yaml.safe_load(raw)
    try:
        reasons = dict(doc["reasons"])
        columns = []
        for spec in doc["columns"]:
            unknown = set(spec) - _KNOWN_COLUMN_KEYS
            if unknown:
                raise ContractError(f"column {spec.get('name')!r}: unknown keys {sorted(unknown)}")
            if spec["type"] not in TYPES:
                raise ContractError(f"column {spec['name']!r}: unknown type {spec['type']!r}")
            nullable = bool(spec.get("nullable", False))
            reason = spec.get("reason")
            if not nullable and reason not in reasons:
                raise ContractError(f"column {spec['name']!r}: reason {reason!r} is not in `reasons`")
            columns.append(
                Column(
                    name=spec["name"],
                    type=spec["type"],
                    nullable=nullable,
                    reason=reason,
                    pattern=re.compile(spec["pattern"]) if spec.get("pattern") else None,
                    min_length=int(spec.get("min_length", 1)),
                    min_items=int(spec.get("min_items", 1)),
                    min_value=spec.get("min_value"),
                    derived_from=tuple(spec.get("derived_from", ())),
                )
            )
        names = [c.name for c in columns]
        if len(set(names)) != len(names):
            raise ContractError("duplicate column names")
        for c in columns:
            missing = set(c.derived_from) - set(names)
            if missing:
                raise ContractError(f"column {c.name!r}: derived_from references unknown columns {sorted(missing)}")
        pk = doc["primary_key"]
        part = doc["partition"]["column"]
        if pk not in names:
            raise ContractError(f"primary_key {pk!r} is not a column")
        if part not in names or columns[names.index(part)].type != "timestamp_utc":
            raise ContractError(f"partition column {part!r} must be a timestamp_utc column")
        pol = doc["policies"]
        if "schema_drift" not in reasons:
            raise ContractError("`reasons` must define schema_drift")
        return Contract(
            name=doc["contract"],
            version=int(doc["version"]),
            sha256=hashlib.sha256(raw).hexdigest(),
            columns=tuple(columns),
            primary_key=pk,
            partition_column=part,
            known_elements=frozenset(doc["known_elements"]),
            reasons=reasons,
            max_quarantine_fraction=float(pol["max_quarantine_fraction"]),
            min_published=_parse_ts(pol["min_published"]),
            max_future_skew_hours=float(pol["max_future_skew_hours"]),
        )
    except KeyError as exc:
        raise ContractError(f"contract is missing key {exc}") from exc


def _is_missing(col: Column, value: Any) -> bool:
    if value is None:
        return True
    if col.type == "string":
        return isinstance(value, str) and len(value.strip()) < col.min_length
    if col.type == "list_string":
        return isinstance(value, list) and len(value) == 0
    return False


def _type_ok(col: Column, value: Any) -> bool:
    if col.type == "string":
        return isinstance(value, str)
    if col.type == "int32":
        return isinstance(value, int) and not isinstance(value, bool) and -(2**31) <= value < 2**31
    if col.type == "list_string":
        return isinstance(value, list) and all(isinstance(v, str) for v in value)
    return isinstance(value, datetime) and value.tzinfo is not None


def violations(
    contract: Contract, row: dict[str, Any], reference_time: datetime | None = None
) -> list[tuple[str, str]]:
    """Return [(reason_code, detail), ...] for every contract rule the row breaks (empty = valid)."""
    found: list[tuple[str, str]] = []

    def add(reason: str, detail: str) -> None:
        found.append((reason, detail))

    flagged: set[str] = set()
    for col in contract.columns:
        value = row.get(col.name)
        if _is_missing(col, value):
            # A derived column is empty *because* its sources are; reporting it too would be noise.
            if not col.nullable and not (col.derived_from and flagged & set(col.derived_from)):
                add(col.reason or "bad_type", f"{col.name} is missing or empty")
                flagged.add(col.name)
            continue
        if not _type_ok(col, value):
            add("bad_type", f"{col.name} has wrong type {type(value).__name__}")
            flagged.add(col.name)
            continue
        reason = col.reason or "bad_type"
        if col.pattern is not None and col.type == "string" and not col.pattern.fullmatch(value):
            add(reason, f"{col.name}={value!r} does not match the contract pattern")
        if col.type == "list_string":
            if len(value) < col.min_items:
                add(reason, f"{col.name} has fewer than {col.min_items} items")
            elif any(not v.strip() for v in value):
                add(reason, f"{col.name} contains an empty item")
        if col.type == "int32" and col.min_value is not None and value < col.min_value:
            add(reason, f"{col.name}={value} is below {col.min_value}")

    pub, upd = row.get("published"), row.get("updated")
    if isinstance(pub, datetime) and pub.tzinfo is not None:
        if pub < contract.min_published:
            add("bad_date", f"published {pub.isoformat()} is before {contract.min_published.isoformat()}")
        if isinstance(upd, datetime) and upd.tzinfo is not None and upd < pub:
            add("bad_date", "updated is earlier than published")
    if reference_time is not None and isinstance(upd, datetime) and upd.tzinfo is not None:
        limit = reference_time + timedelta(hours=contract.max_future_skew_hours)
        if upd > limit:
            add("bad_date", f"updated {upd.isoformat()} is in the future relative to the fetch time")
    return found
