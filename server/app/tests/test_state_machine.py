"""Transizioni coerenti (5.1) e scheduling configurabile / riavvii (5.2)."""
from datetime import timedelta

from sqlalchemy import func, select

from app.adapters.twitch.mock import MockTwitch
from app.core.timeutil import utcnow
from app.db.models import Clip, Event, PublicationJob, StateTransition
from app.domain import states as S
from app.services import collector, factory, pipeline, publishing, queue, reviews

from .conftest import UniqueAnalyzer, set_settings
from .test_publishing import FakePublisher, FakeStorage, prepare_live
from .test_publishing_hardening import Flaky


def illegal(db):
    seen = db.execute(select(StateTransition.entity, StateTransition.from_state, StateTransition.to_state)).all()
    return sorted({(e, f, t) for e, f, t in seen if not S.is_legal(e, f, t)})


def test_guard_reports_but_does_not_block_an_illegal_transition(db):
    from app.services import events

    MockTwitch().path.write_text("[]")
    MockTwitch().spawn("funny_portrait", minutes_ago=30)
    collector.scan(db)
    c = db.scalar(select(Clip))
    events.transition(db, "clip", c, S.RENDERING, "salto non previsto")
    assert c.status == S.RENDERING
    assert db.scalar(select(func.count()).select_from(Event).where(Event.type == "illegal_transition")) == 1


def test_clip_happy_path_is_ordered(db):
    set_settings({"twitch": {"portrait_wait_minutes": 0}})
    factory.OVERRIDES["analyzer"] = UniqueAnalyzer()
    MockTwitch().path.write_text("[]")
    MockTwitch().spawn("funny_portrait", minutes_ago=30)
    collector.scan(db)
    pipeline.run_until_idle(db, 50)
    c = db.scalar(select(Clip))
    seq = [t for (t,) in db.execute(select(StateTransition.to_state).where(StateTransition.entity_id == c.id).order_by(StateTransition.id))]
    wanted = [S.DOWNLOADED, S.ANALYZING, S.ANALYZED, S.RENDERING, S.READY, S.AUTO_READY]
    it = iter(seq)
    assert all(any(x == w for x in it) for w in wanted), seq  # sottosequenza ordinata, nessun salto all'indietro


def test_full_demo_lifecycle_uses_only_legal_transitions(db):
    set_settings({"twitch": {"portrait_wait_minutes": 0}})
    collector.scan(db)
    pipeline.run_until_idle(db, 200)
    publishing.tick(db)
    clips = list(db.scalars(select(Clip).where(Clip.status == S.NEEDS_REVIEW)))
    assert clips
    reviews.approve(db, clips[0].id)
    if len(clips) > 1:
        reviews.reject(db, clips[1].id)
    db.commit()
    pipeline.run_until_idle(db, 50)
    publishing.tick(db)
    jobs = queue.list_jobs(db, [S.QUEUED_LOCAL])
    if jobs:
        queue.update_job(db, jobs[0].id, cancel=True)
        queue.rebuild(db)
    db.commit()
    assert illegal(db) == []
    assert db.scalar(select(func.count()).select_from(Event).where(Event.type == "illegal_transition")) == 0


def test_live_failure_and_recovery_paths_use_only_legal_transitions(db):
    pub = Flaky(exists=True)
    prepare_live(db, 2)
    factory.OVERRIDES.update(publisher=pub, storage=FakeStorage())
    publishing.tick(db)
    for j in queue.list_jobs(db):
        j.next_attempt_at = utcnow() - timedelta(seconds=1)
    db.commit()
    publishing.tick(db)
    for p in pub.posts.values():
        p.status = "sent"
    publishing.tick(db)
    ok = FakePublisher()
    factory.OVERRIDES["publisher"] = ok
    assert illegal(db) == []


def test_publisher_job_states_never_leave_published(db):
    assert S.is_legal("publication", S.QUEUED_LOCAL, S.SCHEDULING) and not S.is_legal("publication", S.PUBLISHED, S.QUEUED_LOCAL)
    # STEP 2.1: AUTO_READY → DOWNLOADED è ora la rianalisi ESPLICITA (`pipeline.reprocess`), non più una transizione «non prevista»
    assert S.is_legal("clip", S.AUTO_READY, S.DOWNLOADED) and S.is_legal("clip", S.FAILED, S.DOWNLOADED)
    assert not S.is_legal("clip", S.AUTO_READY, S.DISCOVERED) and not S.is_legal("clip", S.AUTO_READY, S.WAITING_FOR_MEDIA)


# ------------------------------------------------------------ scheduling
def make_ready(db, n):
    set_settings({"twitch": {"portrait_wait_minutes": 0}})
    factory.OVERRIDES["analyzer"] = UniqueAnalyzer()
    t = MockTwitch()
    t.path.write_text("[]")
    for i in range(n):
        t.spawn("funny_portrait", minutes_ago=100 + i)
    collector.scan(db)
    pipeline.run_until_idle(db, 40 * n)


def test_defaults_are_four_posts_per_day_at_the_documented_slots(db):
    from app.core.settings_model import ProductSettings

    p = ProductSettings().publishing
    assert (p.daily_max, p.slots, p.timezone) == (4, ["10:00", "13:30", "17:30", "21:00"], "Europe/Rome")


def test_number_and_times_are_configurable_and_respect_timezone(db):
    from zoneinfo import ZoneInfo

    set_settings({"publishing": {"daily_max": 2, "slots": ["09:15", "22:40", "12:00"], "timezone": "America/New_York"}})
    make_ready(db, 5)
    queue.rebuild(db)
    jobs = queue.list_jobs(db, [S.QUEUED_LOCAL])
    local = [j.due_at.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo("America/New_York")) for j in jobs]
    assert len(jobs) == 5
    per_day: dict = {}
    for d in local:
        per_day[d.date()] = per_day.get(d.date(), 0) + 1
        assert (d.hour, d.minute) in {(9, 15), (12, 0), (22, 40)}
    assert max(per_day.values()) <= 2  # tetto giornaliero configurabile


def test_restart_keeps_the_same_schedule_without_duplicates(db, env):
    make_ready(db, 6)
    queue.rebuild(db)
    db.commit()
    before = {j.clip_id: j.due_at for j in queue.list_jobs(db)}
    from app.db import base
    from app.db.base import new_session
    from app.main import bootstrap

    base.reset_engine()  # simula il riavvio del processo
    bootstrap()
    db2 = new_session()
    try:
        queue.rebuild(db2)
        queue.rebuild(db2)
        db2.commit()
        after = {j.clip_id: j.due_at for j in queue.list_jobs(db2)}
        assert after == before and db2.scalar(select(func.count()).select_from(PublicationJob)) == len(before)
    finally:
        db2.close()


def test_dst_transition_keeps_local_wall_clock_times():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app.domain.schedule import calculate_next_slots

    start = datetime(2027, 3, 26, 12, 0)  # UTC, due giorni prima del cambio all'ora legale (28/03/2027)
    slots = calculate_next_slots(start, ["10:00", "21:00"], "Europe/Rome", 6, set(), 4, {}, 30)
    rome = [s.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo("Europe/Rome")) for s in slots]
    assert all((r.hour, r.minute) in {(10, 0), (21, 0)} for r in rome)
    assert {s.hour for s in slots} == {9, 20, 8, 19}  # UTC cambia di un'ora dopo il cambio, l'ora locale no
