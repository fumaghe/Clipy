from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    """Datetime UTC naive (SQLite non conserva tzinfo): tutte le date nel DB sono UTC."""
    return datetime.now(UTC).replace(tzinfo=None)


def as_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def naive_utc(dt: datetime) -> datetime:
    return as_utc(dt).replace(tzinfo=None)


def iso(dt: datetime | None) -> str | None:
    return None if dt is None else as_utc(dt).strftime("%Y-%m-%dT%H:%M:%SZ")
