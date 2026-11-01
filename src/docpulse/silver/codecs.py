"""Table codecs: how a list of row dicts becomes bytes in object storage.

Pipeline logic only sees ``TableCodec``. ``ParquetCodec`` is what production uses; ``JsonCodec`` is a
byte-deterministic stand-in so the logic can be tested without pyarrow.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol


@dataclass(frozen=True)
class Field:
    name: str
    type: str  # string | int32 | list_string | timestamp_utc


class TableCodec(Protocol):
    extension: str

    def encode(self, rows: list[dict[str, Any]]) -> bytes: ...

    def decode(self, data: bytes) -> list[dict[str, Any]]: ...


class JsonCodec:
    """Canonical JSON lines. Test stand-in: deterministic, human-readable, no dependencies."""

    extension = "jsonl"

    def __init__(self, fields: Sequence[Field]) -> None:
        self._fields = tuple(fields)

    def encode(self, rows: list[dict[str, Any]]) -> bytes:
        lines = []
        for row in rows:
            out = {}
            for f in self._fields:
                v = row.get(f.name)
                out[f.name] = v.astimezone(timezone.utc).isoformat() if isinstance(v, datetime) else v
            lines.append(json.dumps(out, sort_keys=True, ensure_ascii=False, separators=(",", ":")))
        return ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")

    def decode(self, data: bytes) -> list[dict[str, Any]]:
        rows = []
        for line in data.decode("utf-8").splitlines():
            obj = json.loads(line)
            for f in self._fields:
                if f.type == "timestamp_utc" and obj.get(f.name) is not None:
                    obj[f.name] = datetime.fromisoformat(obj[f.name])
            rows.append(obj)
        return rows


class ParquetCodec:
    """Parquet via pyarrow (imported lazily). Rows must already be sorted by the caller: with a fixed
    row order, fixed schema and fixed settings the file bytes are deterministic for a given pyarrow."""

    extension = "parquet"

    def __init__(self, fields: Sequence[Field]) -> None:
        import pyarrow as pa

        mapping = {
            "string": pa.string(),
            "int32": pa.int32(),
            "list_string": pa.list_(pa.string()),
            "timestamp_utc": pa.timestamp("us", tz="UTC"),
        }
        self._pa = pa
        self._schema = pa.schema([pa.field(f.name, mapping[f.type]) for f in fields])

    def encode(self, rows: list[dict[str, Any]]) -> bytes:
        import pyarrow.parquet as pq

        table = self._pa.Table.from_pylist(rows, schema=self._schema)
        sink = self._pa.BufferOutputStream()
        pq.write_table(table, sink, compression="snappy")
        return sink.getvalue().to_pybytes()

    def decode(self, data: bytes) -> list[dict[str, Any]]:
        import pyarrow.parquet as pq

        return pq.read_table(self._pa.BufferReader(data)).to_pylist()
