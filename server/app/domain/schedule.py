"""Calcolo slot di pubblicazione (funzioni pure, DST-safe)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo


def _local_to_utc(tz: ZoneInfo, day, hh: int, mm: int) -> datetime | None:
    """Converte un orario locale in UTC. Orari inesistenti (salto DST) -> None; ambigui -> prima occorrenza."""
    naive = datetime(day.year, day.month, day.day, hh, mm)
    aware = naive.replace(tzinfo=tz, fold=0)
    back = aware.astimezone(UTC).astimezone(tz)
    if (back.hour, back.minute) != (hh, mm):  # ora inesistente (spring forward)
        return None
    return aware.astimezone(UTC)


def calculate_next_slots(
    now_utc: datetime,
    slots: list[str],
    tz_name: str,
    count: int,
    occupied: set[datetime] | None = None,
    daily_max: int | None = None,
    day_counts: dict | None = None,
    max_days: int = 400,
) -> list[datetime]:
    """Restituisce i prossimi `count` slot liberi (UTC naive), > now_utc.

    - `occupied`: istanti UTC (naive) già assegnati
    - `daily_max`: tetto per giorno locale; `day_counts` = job già presenti per giorno locale (date -> n)
    """
    tz = ZoneInfo(tz_name)
    occupied = {o.replace(microsecond=0) for o in (occupied or set())}
    counts = dict(day_counts or {})
    now = now_utc.replace(tzinfo=UTC) if now_utc.tzinfo is None else now_utc.astimezone(UTC)
    start_day = now.astimezone(tz).date()
    parsed = sorted((int(s[:2]), int(s[3:5])) for s in slots)
    out: list[datetime] = []
    for d in range(max_days):
        day = start_day + timedelta(days=d)
        for hh, mm in parsed:
            if daily_max is not None and counts.get(day, 0) >= daily_max:
                break
            u = _local_to_utc(tz, day, hh, mm)
            if u is None or u <= now:
                continue
            naive = u.replace(tzinfo=None, microsecond=0)
            if naive in occupied:
                continue
            out.append(naive)
            occupied.add(naive)
            counts[day] = counts.get(day, 0) + 1
            if len(out) >= count:
                return out
    return out


def local_day(dt_utc_naive: datetime, tz_name: str):
    return dt_utc_naive.replace(tzinfo=UTC).astimezone(ZoneInfo(tz_name)).date()
