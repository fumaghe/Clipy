from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.adapters.video import ffmpeg
from app.api.deps import DB
from app.api.dto import AnalysisDTO, ReviewDTO, clip_dto, pub_dto
from app.core.config import get_env
from app.core.errors import ClipyError, Conflict, NotFound
from app.db.models import Analysis, Clip, PublicationJob, RenderJob, Review, StateTransition
from app.domain import states as S
from app.services import collector, events, pipeline, queue, reviews
from app.workers import runner

router = APIRouter(prefix="/api/v1/clips", tags=["clips"])


class ImportBody(BaseModel):
    ref: str = Field(min_length=6, max_length=300)


class PatchBody(BaseModel):
    priority: int | None = Field(None, ge=-100, le=100)
    paused: bool | None = None
    template_id: str | None = None


@router.get("")
def list_clips(q: str | None = None, status: str | None = None, game: str | None = None, portrait: bool | None = None,
               page: int = Query(1, ge=1), page_size: int = Query(24, ge=1, le=200), db: Session = DB) -> dict:
    stmt = select(Clip)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Clip.title.ilike(like), Clip.overlay_title.ilike(like), Clip.game_name.ilike(like), Clip.creator_name.ilike(like)))
    if status:
        stmt = stmt.where(Clip.status.in_(status.split(",")))
    if game:
        stmt = stmt.where(Clip.game_name == game)
    if portrait is not None:
        stmt = stmt.where(Clip.is_portrait_file.is_(portrait))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(Clip.created_at_utc.desc()).offset((page - 1) * page_size).limit(page_size))
    return {"items": [clip_dto(db, c).model_dump(mode="json") for c in rows], "total": total, "page": page, "page_size": page_size}


@router.post("/sync")
def sync(db: Session = DB) -> dict:
    res = collector.scan(db)
    res["processing_started"] = runner.process_in_background()
    return res


@router.post("/import")
def import_(body: ImportBody, db: Session = DB) -> dict:
    c = collector.import_clip(db, body.ref)
    runner.process_in_background()
    return clip_dto(db, c).model_dump(mode="json")


@router.get("/{clip_id}")
def get_clip(clip_id: str, db: Session = DB) -> dict:
    c = reviews.get_clip(db, clip_id)
    d = clip_dto(db, c).model_dump(mode="json")
    d["analyses"] = [AnalysisDTO.model_validate(a).model_dump(mode="json") for a in db.scalars(select(Analysis).where(Analysis.clip_id == c.id).order_by(Analysis.created_at.desc()))]
    d["reviews"] = [ReviewDTO.model_validate(r).model_dump(mode="json") for r in db.scalars(select(Review).where(Review.clip_id == c.id).order_by(Review.created_at.desc()))]
    d["render_jobs"] = [{"id": r.id, "status": r.status, "template_id": r.template_id, "reference_verified": r.reference_verified, "checks": r.checks, "warnings": r.warnings, "error": r.error}
                        for r in db.scalars(select(RenderJob).where(RenderJob.clip_id == c.id).order_by(RenderJob.started_at.desc()))]
    d["publications"] = [pub_dto(j).model_dump(mode="json") for j in db.scalars(select(PublicationJob).where(PublicationJob.clip_id == c.id).order_by(PublicationJob.created_at.desc()))]
    d["transitions"] = [{"from": t.from_state, "to": t.to_state, "note": t.note, "at": t.at.isoformat() + "Z"}
                        for t in db.scalars(select(StateTransition).where(StateTransition.entity_id == c.id).order_by(StateTransition.id.desc()).limit(40))]
    d["media_info"] = c.media_info
    return d


@router.patch("/{clip_id}")
def patch_clip(clip_id: str, body: PatchBody, db: Session = DB) -> dict:
    c = reviews.get_clip(db, clip_id)
    if body.priority is not None:
        c.priority = body.priority
    if body.paused is not None:
        c.paused = body.paused
    if body.template_id is not None:
        from app.services.templates import get_template

        get_template(db, body.template_id)
        if c.template_id != body.template_id:
            c.template_id = body.template_id
            if c.status in (S.AUTO_READY, S.APPROVED, S.NEEDS_REVIEW):
                _rerender(db, c)
    return clip_dto(db, c).model_dump(mode="json")


def _rerender(db: Session, c: Clip) -> None:
    if c.status == S.APPROVED:
        c.approve_after_render, c.human_edited = True, True
    pipeline.invalidate_render(db, c)
    runner.process_in_background()


@router.post("/{clip_id}/analyze")
def analyze(clip_id: str, db: Session = DB) -> dict:
    c = reviews.get_clip(db, clip_id)
    if not c.original_path or not Path(c.original_path).exists():
        raise Conflict("Originale non scaricato: usa Retry", code="no_original")
    if c.status in (S.ANALYZING, S.RENDERING):
        raise Conflict("Clip già in elaborazione", code="busy")
    pipeline.invalidate_render(db, c, to_status=S.DOWNLOADED)
    c.error, c.step_attempts = None, 0
    runner.process_in_background()
    return clip_dto(db, c).model_dump(mode="json")


@router.post("/{clip_id}/render")
def render(clip_id: str, db: Session = DB) -> dict:
    c = reviews.get_clip(db, clip_id)
    if not pipeline.latest_analysis(db, c):
        raise Conflict("Esegui prima l'analisi", code="no_analysis")
    if c.status in (S.ANALYZING, S.RENDERING):
        raise Conflict("Clip già in elaborazione", code="busy")
    _rerender(db, c)
    return clip_dto(db, c).model_dump(mode="json")


@router.post("/{clip_id}/retry")
def retry(clip_id: str, db: Session = DB) -> dict:
    c = reviews.get_clip(db, clip_id)
    if c.status != S.FAILED:
        raise Conflict("Retry disponibile solo per clip FAILED", code="bad_state")
    pipeline.retry_clip(db, c)
    runner.process_in_background()
    return clip_dto(db, c).model_dump(mode="json")


@router.post("/{clip_id}/skip")
def skip(clip_id: str, db: Session = DB) -> dict:
    c = reviews.get_clip(db, clip_id)
    for j in db.scalars(select(PublicationJob).where(PublicationJob.clip_id == c.id, PublicationJob.status.in_([S.QUEUED_LOCAL, S.RETRY_PENDING, S.DRY_RUN_OK]))):
        events.transition(db, "publication", j, S.CANCELLED, "clip saltata")
    events.transition(db, "clip", c, S.SKIPPED, "saltata dall'utente")
    return clip_dto(db, c).model_dump(mode="json")


@router.post("/{clip_id}/queue")
def add_to_queue(clip_id: str, db: Session = DB) -> dict:
    c = reviews.get_clip(db, clip_id)
    if c.status != S.AUTO_READY and c.status != S.APPROVED:
        reviews.approve(db, clip_id, note="aggiunta manuale alla coda")
    queue.revive(db, c)
    queue.rebuild(db)
    return clip_dto(db, c).model_dump(mode="json")


def _file(c: Clip, kind: str) -> Path:
    path = c.render_path if kind == "render" else c.original_path
    if not path or not Path(path).exists():
        raise NotFound("File non disponibile", code="file_not_found")
    p = Path(path).resolve()
    if get_env().data_dir.resolve() not in p.parents:  # solo file sotto data/
        raise NotFound("File non disponibile", code="file_not_found")
    return p


@router.get("/{clip_id}/video")
def video(clip_id: str, kind: str = Query("render", pattern="^(render|original)$"), db: Session = DB):
    c = reviews.get_clip(db, clip_id)
    return FileResponse(_file(c, kind), media_type="video/mp4", filename=f"{c.source_clip_id}-{kind}.mp4", content_disposition_type="inline")


@router.get("/{clip_id}/preview")
def preview(clip_id: str, kind: str = Query("render", pattern="^(render|original)$"), db: Session = DB):
    c = reviews.get_clip(db, clip_id)
    try:
        src = _file(c, kind)
    except NotFound:
        src = _file(c, "original")
    out = get_env().dir("previews") / f"thumb-{c.id}-{kind}-{c.content_version}-{src.stat().st_mtime_ns}.jpg"
    if not out.exists():
        r = ffmpeg.run(["ffmpeg", "-y", "-v", "error", "-ss", "1", "-i", str(src), "-frames:v", "1", "-vf", "scale=360:-2", str(out)], 60)
        if r.returncode != 0 or not out.exists():
            r = ffmpeg.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-frames:v", "1", "-vf", "scale=360:-2", str(out)], 60)
            if r.returncode != 0:
                raise ClipyError("Anteprima non generabile", code="preview_failed", status_code=500)
    return FileResponse(out, media_type="image/jpeg")
