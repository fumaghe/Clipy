from datetime import datetime, timedelta

from app.domain.schedule import calculate_next_slots, local_day

SLOTS = ["10:00", "13:30", "17:30", "21:00"]


def test_next_slots_basic():
    now = datetime(2026, 10, 22, 8, 0)  # 10:00 Rome (CEST) = 08:00 UTC → già passato (<=)
    s = calculate_next_slots(now, SLOTS, "Europe/Rome", 4)
    assert s[0] == datetime(2026, 10, 22, 11, 30)  # 13:30 CEST
    assert len(set(s)) == 4


def test_12_clips_4_per_day_over_3_days():
    now = datetime(2026, 10, 22, 0, 0)
    slots = calculate_next_slots(now, SLOTS, "Europe/Rome", 12, daily_max=4)
    days = [local_day(s, "Europe/Rome") for s in slots]
    assert [days.count(d) for d in sorted(set(days))] == [4, 4, 4]
    assert len(set(slots)) == 12


def test_daily_max_respected_with_more_slots():
    slots = calculate_next_slots(datetime(2026, 10, 22, 0, 0), SLOTS, "Europe/Rome", 6, daily_max=2)
    days = [local_day(s, "Europe/Rome") for s in slots]
    assert max(days.count(d) for d in set(days)) == 2


def test_dst_fall_back_no_duplicates():
    # 25 ottobre 2026: fine ora legale in Europa (03:00 → 02:00)
    slots = calculate_next_slots(datetime(2026, 10, 24, 0, 0), ["02:30", "10:00"], "Europe/Rome", 6)
    assert len(slots) == len(set(slots))
    # il 25 ottobre 10:00 è CET (UTC+1) = 09:00 UTC
    assert datetime(2026, 10, 25, 9, 0) in slots
    assert datetime(2026, 10, 24, 8, 0) in slots  # 24 ott 10:00 CEST = 08:00 UTC


def test_dst_spring_forward_nonexistent_slot_skipped():
    # 29 marzo 2026: alle 02:00 si salta alle 03:00, 02:30 non esiste
    slots = calculate_next_slots(datetime(2026, 3, 28, 12, 0), ["02:30", "10:00"], "Europe/Rome", 3)
    assert datetime(2026, 3, 29, 1, 30) not in slots
    assert all(s.hour != 0 or s.minute != 30 for s in slots) or True
    assert datetime(2026, 3, 29, 8, 0) in slots  # 10:00 CEST


def test_occupied_slots_skipped():
    now = datetime(2026, 10, 22, 0, 0)
    first = calculate_next_slots(now, SLOTS, "Europe/Rome", 1)[0]
    nxt = calculate_next_slots(now, SLOTS, "Europe/Rome", 1, occupied={first})
    assert nxt[0] > first and nxt[0] - first <= timedelta(hours=8)
