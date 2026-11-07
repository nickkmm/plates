"""Colombia time for what people read; UTC for what is stored.

The containers and PostgreSQL run in UTC and the timestamp columns are naive
(TIMESTAMP, not TIMESTAMPTZ), so a stored timestamp is always naive UTC. Convert
it only when showing it, never when storing it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

BOGOTA_TZ = ZoneInfo("America/Bogota")


def utc_now() -> datetime:
    """Now as it is stored: naive UTC, same as PostgreSQL's now() in UTC."""
    return datetime.now(UTC).replace(tzinfo=None)


def to_bogota(value: datetime) -> datetime:
    """`value` in Colombia time. A naive value is a stored timestamp, so UTC."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(BOGOTA_TZ)


def format_bogota(value: datetime | None, pattern: str = "%d/%m/%Y %H:%M") -> str:
    """Jinja filter `bogota`: a timestamp as Colombia time; "" when there is none."""
    return to_bogota(value).strftime(pattern) if value else ""
