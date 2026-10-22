from datetime import timedelta
from pathlib import Path

from sqlalchemy import select

from app.adapters.twitch.mock import MockTwitch
from app.adapters.video import ffmpeg
from app.core.timeutil import utcnow
from app.db.models import Clip, PublicationJob
from app.domain import states as S
from app.domain.schedule import local_day
from app.services import collector, factory, pipeline, publishing, queue, reviews

from .conftest import UniqueAnalyzer, set_settings


def spawn_n(n, key="funny_portrait"):
    t = MockTwitch()
    for i in range(n):
        t.spawn(key, minutes_ago=100 + i)


def test_12_clips_ready_and_scheduled_4_per_day_3_days(db):
    factory.OVERRIDES["analyzer"] = UniqueAnalyzer()
    spawn_n(12)
    collector.scan(db)
    pipeline.run_until_idle(db, 200)
    clips = list(db.scalars(select(Clip).where(Clip.status.not_in(["DISCOVERED"]))))
    assert [c.status for c in clips].count(S.AUTO_READY) >= 12, [(c.source_clip_id, c.status, c.error) for c in clips]
    queue.rebuild(db)
    db.commit()
    jobs = queue.list_jobs(db, [S.QUEUED_LOCAL])
    ready = db.scalars(select(Clip).where(Clip.status == S.AUTO_READY)).all()
    assert len(jobs) == len(ready)
    due = [j.due_at for j in jobs]
    assert len(set(due)) == len(due)  # nessuna sovrapposizione
    per_day = {}
    for j in jobs:
        d = local_day(j.due_at, "Europe/Rome")
        per_day[d] = per_day.get(d, 0) + 1
    assert max(per_day.values()) <= 4
    first12 = sorted(per_day.items())[:3]
    assert [n for _, n in first12][1:3] == [4, 4]
    # idempotente
    queue.rebuild(db)
    assert len(queue.list_jobs(db, [S.QUEUED_LOCAL])) == len(jobs)


def test_review_gate_8_ok_4_doubtful_do_not_block_queue(db):
    factory.OVERRIDES["analyzer"] = UniqueAnalyzer()
    spawn_n(8)
    t = MockTwitch()
    t.spawn("blocked_personal", minutes_ago=90)
    t.spawn("blocked_personal", minutes_ago=91)
    t.spawn("ambiguous_unclear", minutes_ago=92)
    t.spawn("ambiguous_unclear", minutes_ago=93)
    set_settings({"twitch": {"portrait_wait_minutes": 0}})
    collector.scan(db)
    pipeline.run_until_idle(db, 300)
    queue.rebuild(db)
    db.commit()
    rev = reviews.inbox(db)
    assert len(rev) >= 4
    queued_ids = {j.clip_id for j in queue.list_jobs(db, [S.QUEUED_LOCAL])}
    assert not queued_ids & {c.id for c in rev}  # il dubbio non viene mai pianificato senza approvazione
    assert len(queued_ids) >= 8
    # approvazione → entra in coda; reject → mai
    a, b = rev[0], rev[1]
    reviews.approve(db, a.id)
    reviews.reject(db, b.id)
    db.commit()
    queue.rebuild(db)
    ids = {j.clip_id for j in queue.list_jobs(db, [S.QUEUED_LOCAL])}
    assert a.id in ids and b.id not in ids
    db.refresh(b)
    assert b.status == S.REJECTED and Path(b.original_path).exists() and Path(b.render_path).exists()  # audit e file conservati


def test_silent_clip_not_discarded_and_no_invented_dialogue(db):
    collector.scan(db)
    pipeline.run_until_idle(db, 100)
    c = db.scalar(select(Clip).where(Clip.source_clip_id.like("%silent_gameplay")))
    a = pipeline.latest_analysis(db, c)
    assert a.transcript == "" and c.status == S.AUTO_READY and c.render_path


def test_edit_overlay_invalidates_render_without_redownload(db):
    collector.scan(db)
    pipeline.run_until_idle(db, 100)
    c = db.scalar(select(Clip).where(Clip.source_clip_id.like("%funny_portrait")))
    orig, old_render, checksum = c.original_path, c.render_path, c.checksum
    reviews.edit_content(db, c.id, overlay_title="Nuovo titolo con carrello", caption=None, hashtags=None, approve_after=True)
    db.commit()
    assert c.render_path is None and c.status == S.ANALYZED
    pipeline.run_until_idle(db, 20)
    db.refresh(c)
    assert c.status == S.APPROVED and c.render_path != old_render and c.original_path == orig and c.checksum == checksum


def test_regenerate_does_not_redownload(db):
    collector.scan(db)
    pipeline.run_until_idle(db, 100)
    c = db.scalar(select(Clip).where(Clip.source_clip_id.like("%funny_portrait")))
    before, attempts = c.overlay_title, c.download_attempts
    reviews.regenerate(db, c.id)
    db.commit()
    assert c.overlay_title != before and c.download_attempts == attempts and c.status == S.ANALYZED


def test_restart_during_render_resumes(db):
    MockTwitch().path.write_text("[]")
    MockTwitch().spawn("funny_portrait", minutes_ago=10)
    collector.scan(db)
    c = db.scalar(select(Clip))
    pipeline.run_until_idle(db, 2)  # download + analisi
    c.status, c.locked_until = S.RENDERING, utcnow() - timedelta(minutes=1)  # processo morto a metà render (lock scaduto)
    db.commit()
    pipeline.run_until_idle(db, 20)
    db.refresh(c)
    assert c.status in (S.AUTO_READY, S.NEEDS_REVIEW) and Path(c.render_path).exists()
    assert not list(Path(c.render_path).parent.glob("*.part*"))


def test_dry_run_zero_remote_calls(db):
    collector.scan(db)
    pipeline.run_until_idle(db, 100)

    class Spy:
        name, simulated = "spy", False
        calls = 0

        def __getattr__(self, n):
            Spy.calls += 1
            raise AssertionError("nessuna chiamata remota in dry-run")

    factory.OVERRIDES["publisher"] = Spy()
    publishing.tick(db)
    jobs = queue.list_jobs(db)
    assert jobs and all(j.status == S.DRY_RUN_OK and j.simulated for j in jobs) and Spy.calls == 0


def test_mock_publisher_lifecycle_scheduled_then_published(db):
    set_settings({"publishing": {"provider": "mock"}})
    collector.scan(db)
    pipeline.run_until_idle(db, 100)
    publishing.tick(db)
    j = queue.list_jobs(db, [S.SCHEDULED_REMOTE])[0]
    assert j.simulated and j.remote_post_id and j.status == S.SCHEDULED_REMOTE  # NON published
    # forzo la scadenza dello slot remoto simulato
    import json

    from app.adapters.publisher.mock import MockPublisher

    mp = MockPublisher()
    d = json.loads(mp.path.read_text())
    d["posts"][j.remote_post_id]["due_at"] = (utcnow() - timedelta(minutes=1)).isoformat()
    mp.path.write_text(json.dumps(d))
    publishing.reconcile_job(db, j)
    assert j.status == S.PUBLISHED


def test_video_files_valid_vertical(db):
    collector.scan(db)
    pipeline.run_until_idle(db, 100)
    for c in db.scalars(select(Clip).where(Clip.render_path.is_not(None))):
        info = ffmpeg.probe(Path(c.render_path))
        assert (info["width"], info["height"]) == (1080, 1920) and info["vcodec"] == "h264" and info["pix_fmt"] == "yuv420p"
        assert info["has_audio"] == bool(c.media_info.get("has_audio"))


def test_asr_unavailable_does_not_invent_text(db):
    from app.adapters.ai.base import Transcript

    class NoAsr:
        def transcribe(self, video, lang):
            return Transcript("", [], "", "faster-whisper", available=False, note="non installato")

    factory.OVERRIDES["transcriber"] = NoAsr()
    set_settings({"ai": {"provider": "mock"}})
    collector.scan(db)
    pipeline.run_until_idle(db, 100)
    c = db.scalar(select(Clip).where(Clip.source_clip_id.like("%noisy_reaction")))
    a = pipeline.latest_analysis(db, c)
    assert a.transcript == "" and a.asr_available is False


def test_unused_imports():
    _ = (PublicationJob,)
