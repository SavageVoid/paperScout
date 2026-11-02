"""Adapters onto the Day 1 / Day 2 code.

This is the only file in the package that touches code I could not see while
writing it. Every assumption is written down here, checked at runtime, and
reported by `scripts/day3_preflight.py` so a mismatch is one clear error message
instead of a stack trace at 2am.

Assumptions (all verified by the preflight):
  A1  docpulse.config.StorageSettings.from_env() exists
  A2  docpulse.storage.S3Store.from_settings(settings) exists
  A3  the store exposes list_keys / get_bytes / put_bytes / delete_prefix
  A4  docpulse.silver.build.MANIFEST_KEY names the silver manifest
      (fallback: scan silver/ for *_manifest.json)
  A5  the silver manifest has `months: [{key: ...}]`
  A6  month files decode to a list of dicts with a paper id, `version`,
      `title`, `abstract` and (Day 2) `text_hash`
  A7  docpulse.silver.codecs.ParquetCodec / contract.paper_fields exist
      (fallback: read Parquet with pyarrow directly)
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from typing import Any, Iterable

# Candidate spellings for the paper-id column, most likely first.
ID_KEYS = ("paper_id", "id", "arxiv_id", "id_full", "arxiv_id_full")
VERSION_KEYS = ("version", "v", "paper_version")
HASH_KEYS = ("text_hash", "content_hash", "text_sha256")


class CompatError(RuntimeError):
    """An assumption in this module did not hold. The message says which."""


@dataclass
class Assumption:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class SilverSnapshot:
    manifest_key: str
    manifest: dict
    rows: list[dict] = field(default_factory=list)
    decode_mode: str = "unknown"
    assumptions: list[Assumption] = field(default_factory=list)

    @property
    def n_rows(self) -> int:
        return len(self.rows)

    def summary(self) -> dict:
        return {
            "manifest_key": self.manifest_key,
            "decode_mode": self.decode_mode,
            "n_rows": self.n_rows,
            "n_months": len(self.manifest.get("months") or []),
            "n_papers_total": self.manifest.get("n_papers_total"),
            "contract_version": self.manifest.get("contract_version"),
        }


# --------------------------------------------------------------------------
# storage
# --------------------------------------------------------------------------
def get_store(assumptions: list[Assumption] | None = None):
    """Return the project's S3Store, or explain precisely why it can't."""
    notes = assumptions if assumptions is not None else []
    try:
        from docpulse.config import StorageSettings  # type: ignore
    except Exception as exc:  # pragma: no cover - depends on their repo
        raise CompatError(
            f"A1 failed: cannot import docpulse.config.StorageSettings ({exc}). "
            "Is PYTHONPATH set to the mounted src dir?"
        ) from exc

    try:
        from docpulse.storage import S3Store  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise CompatError(f"A2 failed: cannot import docpulse.storage.S3Store ({exc})") from exc

    settings = None
    try:
        settings = StorageSettings.from_env()
        notes.append(Assumption("A1 StorageSettings.from_env()", True))
    except Exception as exc:  # pragma: no cover
        notes.append(Assumption("A1 StorageSettings.from_env()", False, str(exc)))

    store = None
    try:
        store = S3Store.from_settings(settings) if settings is not None else S3Store()
        notes.append(Assumption("A2 S3Store.from_settings()", True))
    except Exception as exc:  # pragma: no cover
        notes.append(Assumption("A2 S3Store.from_settings()", False, str(exc)))
        raise CompatError(
            f"A2 failed: cannot construct S3Store ({exc}). "
            "If your classmethod has a different name, tell me and I'll adapt this one line."
        ) from exc

    missing = [m for m in ("list_keys", "get_bytes", "put_bytes") if not hasattr(store, m)]
    if missing:
        raise CompatError(f"A3 failed: S3Store has no {missing}")
    notes.append(Assumption("A3 S3Store.list_keys/get_bytes/put_bytes", True))
    return store


# --------------------------------------------------------------------------
# silver
# --------------------------------------------------------------------------
def _readable(store, key: str) -> bool:
    """True if the object exists and can be read. Cheap: the manifest is a few KB."""
    try:
        store.get_bytes(key)
        return True
    except Exception:
        return False


def find_silver_manifest_key(store, assumptions: list[Assumption] | None = None) -> str:
    """Find the silver manifest.

    Importing MANIFEST_KEY is not enough: on a repo where silver has never run (or
    where the constant was renamed), the import succeeds and the object is absent,
    which would surface later as a bare KeyError/botocore error. So the imported
    key is verified against the store before it is trusted.
    """
    notes = assumptions if assumptions is not None else []
    imported: str | None = None
    try:  # A4, preferred path
        from docpulse.silver.build import MANIFEST_KEY  # type: ignore

        imported = str(MANIFEST_KEY)
    except Exception as exc:
        notes.append(Assumption("A4 silver.build.MANIFEST_KEY", False, f"import failed: {exc}"))

    if imported and _readable(store, imported):
        notes.append(Assumption("A4 silver.build.MANIFEST_KEY", True, imported))
        return imported
    if imported:
        notes.append(
            Assumption(
                "A4 silver.build.MANIFEST_KEY",
                False,
                f"imported as {imported} but that object is not in the store",
            )
        )

    keys = [k for k in store.list_keys("silver/") if k.endswith("_manifest.json")]
    if not keys:
        raise CompatError(
            "A4 failed: no silver manifest found. "
            + (f"MANIFEST_KEY={imported!r} is not present in the store, and " if imported else "")
            + "no key under silver/ ends with _manifest.json. Run `make silver` first, "
            "or tell me where the manifest actually lives."
        )
    keys.sort(key=lambda k: (k.count("/"), len(k)))
    notes.append(Assumption("A4 fallback: scan silver/ for *_manifest.json", True, keys[0]))
    return keys[0]


def _their_parquet_codec():  # pragma: no cover - depends on their repo
    """Best effort: build their codec so we decode exactly as Day 2 does."""
    try:
        from docpulse.silver.codecs import ParquetCodec  # type: ignore
        from docpulse.silver.contract import load_contract  # type: ignore
        from docpulse.silver.build import paper_fields  # type: ignore
        from docpulse.silver.tasks import default_contract_path  # type: ignore
    except Exception:
        return None
    try:
        return ParquetCodec(paper_fields(load_contract(default_contract_path())))
    except Exception:
        return None


def _decode_month(store, key: str) -> tuple[list[dict], str]:
    data = store.get_bytes(key)
    if key.endswith(".parquet"):
        try:
            import pyarrow.parquet as pq  # type: ignore

            table = pq.read_table(io.BytesIO(data))
            return table.to_pylist(), "pyarrow"
        except ImportError:
            pass
        codec = _their_parquet_codec()
        if codec is not None:
            return _as_rows(codec.decode(data)), "docpulse.ParquetCodec"
        raise CompatError(
            "A6 failed: cannot read Parquet (no pyarrow, and docpulse's ParquetCodec "
            "was not importable). Run inside the airflow-scheduler container or the "
            "embedder container, both of which have pyarrow."
        )
    if key.endswith((".json", ".jsonl")):
        if key.endswith(".jsonl"):
            return [json.loads(line) for line in data.decode("utf-8").splitlines() if line.strip()], "jsonl"
        payload = json.loads(data)
        if isinstance(payload, dict) and "rows" in payload:
            payload = payload["rows"]
        return _as_rows(payload), "json"
    raise CompatError(f"A6 failed: don't know how to decode {key!r}")


def _as_rows(payload: Any) -> list[dict]:
    """Accept list[dict] or dict-of-columns and return list[dict]."""
    if isinstance(payload, list):
        return [dict(r) for r in payload]
    if isinstance(payload, dict):
        cols = list(payload)
        n = len(payload[cols[0]]) if cols else 0
        return [{c: payload[c][i] for c in cols} for i in range(n)] if n else []
    raise CompatError(f"cannot interpret decoded payload of type {type(payload)}")


def _pick(row: dict, keys: Iterable[str]) -> Any:
    for k in keys:
        if k in row and row[k] not in (None, ""):
            return row[k]
    return None


def normalise_row(row: dict) -> dict | None:
    """Map whatever silver calls its columns onto the five keys we need."""
    paper_id = _pick(row, ID_KEYS)
    if paper_id is None:
        return None
    version = _pick(row, VERSION_KEYS)
    try:
        version = int(version) if version is not None else 1
    except (TypeError, ValueError):
        version = 1
    title = (row.get("title") or "").strip() if isinstance(row.get("title"), str) else ""
    abstract = (row.get("abstract") or "").strip() if isinstance(row.get("abstract"), str) else ""
    return {
        "paper_id": str(paper_id).strip(),
        "version": version,
        "text_hash": _pick(row, HASH_KEYS),
        "title": title,
        "abstract": abstract,
    }


def load_silver(store, assumptions: list[Assumption] | None = None) -> SilverSnapshot:
    notes = assumptions if assumptions is not None else []
    key = find_silver_manifest_key(store, notes)
    manifest = json.loads(store.get_bytes(key))
    if not isinstance(manifest, dict):
        raise CompatError(f"A5 failed: silver manifest at {key} is not a JSON object")

    months = manifest.get("months") or manifest.get("parts") or manifest.get("files") or []
    if not isinstance(months, list):
        raise CompatError("A5 failed: manifest.months is not a list")
    notes.append(Assumption("A5 manifest.months", True, f"{len(months)} entries"))

    rows: list[dict] = []
    modes: set[str] = set()
    for item in months:
        if isinstance(item, str):
            mkey = item
        elif isinstance(item, dict):
            mkey = item.get("key") or item.get("path")
        else:
            raise CompatError(f"A5 failed: unexpected months entry {item!r}")
        if not mkey:
            continue
        decoded, mode = _decode_month(store, mkey)
        modes.add(mode)
        for raw in decoded:
            norm = normalise_row(raw)
            if norm is not None:
                rows.append(norm)

    mode_str = ",".join(sorted(modes)) or "empty"
    notes.append(Assumption("A6 month files decode to rows", True, f"{len(rows)} rows via {mode_str}"))
    return SilverSnapshot(
        manifest_key=key,
        manifest=manifest,
        rows=rows,
        decode_mode=mode_str,
        assumptions=notes,
    )
