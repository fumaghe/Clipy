from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ClipyError, Conflict, NotFound
from app.core.timeutil import utcnow
from app.db.models import Clip, PublicationJob, Review
from app.domain import captions, review_gate
from app.domain import states as S
from app.services import events, pipeline
from app.services import settings_service as ss


def get_clip(db: Session, clip_id: str) -> Clip:
    c = db.get(Clip, clip_id)
    if c is None:
        raise NotFound("Clip non trovata", code="clip_not_found")
    return c


def latest_review(db: Session, clip_id: str) -> Review | None:
    return db.scalar(select(Review).where(Review.clip_id == clip_id).order_by(Review.created_at.desc(), Review.id.desc()))


def _cancel_local_jobs(db: Session, clip: Clip, note: str) -> None:
    for j in db.scalars(select(PublicationJob).where(PublicationJob.clip_id == clip.id, PublicationJob.status.in_([S.QUEUED_LOCAL, S.RETRY_PENDING, S.DRY_RUN_OK]))):
        events.transition(db, "publication", j, S.CANCELLED, note)


def approve(db: Session, clip_id: str, actor: str = "owner", note: str | None = None) -> Clip:
    clip = get_clip(db, clip_id)
    if clip.status not in (S.NEEDS_REVIEW, S.REJECTED, S.AUTO_READY, S.SKIPPED):
        raise Conflict(f"Impossibile approvare una clip in stato {clip.status}", code="bad_state")
    if not clip.render_path or not Path(clip.render_path).exists():
        raise Conflict("Render non disponibile: esegui il render prima di approvare", code="no_render")
    prev = latest_review(db, clip.id)
    db.add(Review(clip_id=clip.id, policy_version=review_gate.POLICY_VERSION, review_state=S.APPROVED, reasons=prev.reasons if prev else [],
                  flags=prev.flags if prev else [], human_override=True, actor=actor, note=note, reviewed_at=utcnow()))
    events.transition(db, "clip", clip, S.APPROVED, f"approvata da {actor}")
    events.emit(db, "review", "approved", "Clip approvata: entra in coda", clip_id=clip.id)
    return clip


def reject(db: Session, clip_id: str, actor: str = "owner", note: str | None = None) -> Clip:
    clip = get_clip(db, clip_id)
    if clip.status in (S.REJECTED,):
        return clip
    _cancel_local_jobs(db, clip, "clip scartata")
    db.add(Review(clip_id=clip.id, policy_version=review_gate.POLICY_VERSION, review_state=S.REJECTED, human_override=True, actor=actor, note=note, reviewed_at=utcnow()))
    events.transition(db, "clip", clip, S.REJECTED, f"scartata da {actor}")  # file e audit restano
    events.emit(db, "review", "rejected", "Clip scartata (file e audit conservati)", clip_id=clip.id)
    return clip


def edit_content(db: Session, clip_id: str, *, overlay_title: str | None, caption: str | None, hashtags: list[str] | None,
                 approve_after: bool = False, actor: str = "owner") -> Clip:
    clip = get_clip(db, clip_id)
    st = ss.get_settings(db)
    changes: dict = {}
    if overlay_title is not None:
        t = captions.truncate(captions.clean_text(overlay_title), st.ai.max_overlay_chars)
        if not t:
            raise ClipyError("Il titolo overlay non può essere vuoto", code="empty_overlay", status_code=422)
        changes["overlay_title"] = [clip.overlay_title, t]
        clip.overlay_title = t
    if caption is not None:
        changes["caption"] = [clip.caption, caption]
        clip.caption = captions.truncate(captions.clean_text(caption), st.publishing.max_caption_length)
    if hashtags is not None:
        clip.hashtags = captions.normalize_hashtags(hashtags, st.ai.max_hashtags)
        changes["hashtags"] = clip.hashtags
    if not changes:
        return clip
    clip.content_version += 1
    clip.human_edited = True
    clip.approve_after_render = approve_after
    db.add(Review(clip_id=clip.id, policy_version=review_gate.POLICY_VERSION, review_state=clip.status, human_override=True, actor=actor,
                  changes=changes, reviewed_at=utcnow(), note="modifica contenuto"))
    # solo il testo overlay richiede un nuovo render; caption/hashtag aggiornano i job locali
    if "overlay_title" in changes:
        pipeline.invalidate_render(db, clip)
    else:
        for j in db.scalars(select(PublicationJob).where(PublicationJob.clip_id == clip.id, PublicationJob.status == S.QUEUED_LOCAL)):
            j.caption, j.hashtags = clip.caption or "", clip.hashtags
        if approve_after and clip.status == S.NEEDS_REVIEW:
            approve(db, clip.id, actor)
            clip.approve_after_render = False
    events.emit(db, "review", "edited", "Contenuto modificato manualmente", clip_id=clip.id)
    return clip


def regenerate(db: Session, clip_id: str) -> Clip:
    clip = get_clip(db, clip_id)
    pipeline.regenerate_content(db, clip)
    return clip


def inbox(db: Session) -> list[Clip]:
    return list(db.scalars(select(Clip).where(Clip.status == S.NEEDS_REVIEW).order_by(Clip.created_at_utc.asc())))
