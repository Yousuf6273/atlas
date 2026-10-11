"""UTC time helpers. All persisted timestamps are ISO-8601 UTC strings."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


def utcnow() -> datetime:
    return datetime.now(UTC)


def now_iso() -> str:
    return utcnow().isoformat(timespec="seconds")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def older_than(value: str | None, *, days: float = 0, hours: float = 0, seconds: float = 0) -> bool:
    dt = parse_iso(value)
    if dt is None:
        return True
    return utcnow() - dt > timedelta(days=days, hours=hours, seconds=seconds)
