"""Coda locale (fonte di verità): FIFO tra clip AUTO_READY/APPROVED su slot configurabili."""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import ClipyError, NotFound
from app.core.timeutil import naive_utc, utcnow
from app.db.models import Clip, PublicationJob
from app.domain import captions
from app.domain import states as S
from app.domain.schedule import calculate_next_slots, local_day
from app.services import events
from app.services import settings_service as ss

GRACE = timedelta(minutes=2)
HORIZON_DAYS = 90


def _channel(st) -> str:
    return st.publishing.channel_id or "default"


def rebuild(db: Session, now=None) -> dict:
    """Idempotente: crea job per le nuove clip idonee e compatta/riassegna i job locali, senza toccare quelli remoti."""
    now = now or utcnow()
    st = ss.get_settings(db)
    pub = st.publishing
    channel = _channel(st)
    jobs = list(db.scalars(select(PublicationJob).where(PublicationJob.target_channel_id == channel)))
    # una clip con un job (qualunque stato) per la versione di contenuto corrente non ne ottiene un secondo:
    # vincolo (clip, canale, versione). I job annullati/falliti si riattivano solo con azione esplicita (revive/retry).
    clips_by_id = {c.id: c for c in db.scalars(select(Clip).where(Clip.id.in_({j.clip_id for j in jobs}))) } if jobs else {}
    live_clip_ids = {j.clip_id for j in jobs if clips_by_id.get(j.clip_id) is not None and j.attempt_version == clips_by_id[j.clip_id].content_version}
    movable = [j for j in jobs if j.status == S.QUEUED_LOCAL and not j.paused and j.locked_until is None]
    fixed = [j for j in jobs if j.status in S.PUB_OCCUPY_SLOT and j not in movable]
    max_pos = max([j.position for j in jobs] or [0])

    eligible = list(db.scalars(select(Clip).where(Clip.status.in_(S.CLIP_ELIGIBLE), Clip.paused.is_(False)).order_by(Clip.created_at_utc.asc())))
    new_clips = [c for c in eligible if c.id not in live_clip_ids and c.render_path and Path(c.render_path).exists()]
    # job locali di clip non più idonee → cancellati
    elig_ids = {c.id for c in eligible}
    for j in list(movable):
        if j.clip_id not in elig_ids:
            events.transition(db, "publication", j, S.CANCELLED, "clip non più idonea")
            movable.remove(j)

    new_jobs = []
    for c in new_clips:
        max_pos += 1
        j = PublicationJob(clip_id=c.id, provider=pub.provider, target_channel_id=channel, attempt_version=c.content_version,
                           caption=c.caption or "", hashtags=c.hashtags or [], due_at=now, status=S.QUEUED_LOCAL, position=max_pos,
                           content_version=c.content_version, simulated=pub.provider == "mock")
        db.add(j)
        new_jobs.append(j)
    db.flush()
    pool = movable + new_jobs
    prio = {c.id: c.priority for c in eligible}
    pool.sort(key=lambda j: (-prio.get(j.clip_id, 0), j.position))

    occupied = {j.due_at for j in fixed}
    day_counts: dict = {}
    for j in fixed:
        d = local_day(j.due_at, pub.timezone)
        day_counts[d] = day_counts.get(d, 0) + 1
    slots = calculate_next_slots(now + GRACE, pub.slots, pub.timezone, len(pool), occupied, pub.daily_max, day_counts, HORIZON_DAYS)
    for j, slot in zip(pool, slots, strict=False):
        j.due_at = slot
    db.flush()
    events.emit(db, "queue", "rebuild", f"Coda ricostruita: {len(new_jobs)} nuove, {len(pool)} locali pianificate")
    return {"new": len(new_jobs), "planned": len(pool)}


def revive(db: Session, clip: Clip) -> PublicationJob | None:
    """Azione esplicita «Aggiungi alla coda»: riattiva il job annullato/fallito della versione corrente."""
    st = ss.get_settings(db)
    j = db.scalar(select(PublicationJob).where(PublicationJob.clip_id == clip.id, PublicationJob.target_channel_id == _channel(st),
                                               PublicationJob.attempt_version == clip.content_version))
    if j and j.status in (S.CANCELLED, S.PUB_FAILED) and not j.remote_post_id:
        j.asset_url, j.error, j.next_attempt_at, j.paused, j.due_at = None, None, None, False, utcnow()
        events.transition(db, "publication", j, S.QUEUED_LOCAL, "riattivato manualmente")
    return j


def list_jobs(db: Session, statuses: list[str] | None = None) -> list[PublicationJob]:
    q = select(PublicationJob).order_by(PublicationJob.due_at.asc())
    if statuses:
        q = q.where(PublicationJob.status.in_(statuses))
    return list(db.scalars(q))


def get_job(db: Session, job_id: str) -> PublicationJob:
    j = db.get(PublicationJob, job_id)
    if j is None:
        raise NotFound("Job di pubblicazione non trovato", code="job_not_found")
    return j


def update_job(db: Session, job_id: str, *, paused: bool | None = None, caption: str | None = None, hashtags: list[str] | None = None,
               swap_with: str | None = None, cancel: bool = False, due_at=None) -> PublicationJob:
    j = get_job(db, job_id)
    st = ss.get_settings(db)
    editable = j.status in (S.QUEUED_LOCAL, S.RETRY_PENDING)
    if cancel:
        if j.status in (S.SCHEDULED_REMOTE, S.SCHEDULING, S.UPLOADING_ASSET):
            raise ClipyError("Il post è già pianificato su Buffer: annullalo da Buffer, poi riconcilia", code="remote_scheduled", status_code=409)
        events.transition(db, "publication", j, S.CANCELLED, "annullato dall'utente")
        return j
    if paused is not None:
        j.paused = paused
        if not paused and j.due_at <= utcnow():
            j.due_at = utcnow()  # il rebuild riassegna uno slot
    if caption is not None or hashtags is not None:
        if not editable:
            raise ClipyError("Caption modificabile solo prima della pianificazione remota", code="not_editable", status_code=409)
        if caption is not None:
            j.caption = captions.truncate(captions.clean_text(caption), st.publishing.max_caption_length)
            j.clip.caption = j.caption
        if hashtags is not None:
            j.hashtags = captions.normalize_hashtags(hashtags, st.ai.max_hashtags)
            j.clip.hashtags = j.hashtags
    if swap_with:
        o = get_job(db, swap_with)
        if not (editable and o.status in (S.QUEUED_LOCAL, S.RETRY_PENDING)):
            raise ClipyError("Solo i job locali possono essere riordinati", code="not_editable", status_code=409)
        j.due_at, o.due_at = o.due_at, j.due_at
        j.position, o.position = o.position, j.position
    if due_at is not None:
        if not editable:
            raise ClipyError("Slot modificabile solo per job locali", code="not_editable", status_code=409)
        new = naive_utc(due_at)
        clash = db.scalar(select(PublicationJob).where(PublicationJob.due_at == new, PublicationJob.id != j.id, PublicationJob.status.in_(S.PUB_OCCUPY_SLOT)))
        if clash:
            raise ClipyError("Slot già occupato", code="slot_taken", status_code=409)
        j.due_at = new
    db.flush()
    _ = st
    return j


def daily_counts(db: Session) -> dict:
    st = ss.get_settings(db)
    rows = db.execute(select(PublicationJob.due_at).where(PublicationJob.status.in_(S.PUB_OCCUPY_SLOT)))
    out: dict[str, int] = {}
    for (d,) in rows:
        k = local_day(d, st.publishing.timezone).isoformat()
        out[k] = out.get(k, 0) + 1
    return out


def count_status(db: Session, status: str) -> int:
    return db.scalar(select(func.count()).select_from(PublicationJob).where(PublicationJob.status == status)) or 0
