"""Time helpers shared across modules."""

from __future__ import annotations

from datetime import datetime, timezone


def as_utc(value: datetime) -> datetime:
    """Return a plain stdlib, timezone-aware UTC datetime.

    Airflow hands tasks pendulum datetimes, and arithmetic on those can come back without a
    usable tzinfo. Round-tripping through ISO text yields an exact stdlib datetime, so nothing
    downstream depends on pendulum behaviour.
    """
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"datetime must be timezone-aware: {value!r}")
    return datetime.fromisoformat(value.isoformat()).astimezone(timezone.utc)
