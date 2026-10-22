from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import DB
from app.api.dto import clip_dto
from app.services import queue, reviews
from app.workers import runner

router = APIRouter(prefix="/api/v1/reviews", tags=["reviews"])


class ContentBody(BaseModel):
    overlay_title: str | None = Field(None, max_length=300)
    caption: str | None = Field(None, max_length=5000)
    hashtags: list[str] | None = None
    approve: bool = False


class NoteBody(BaseModel):
    note: str | None = Field(None, max_length=500)


def _detail(db: Session, clip_id: str) -> dict:
    from app.services import pipeline

    c = reviews.get_clip(db, clip_id)
    d = clip_dto(db, c).model_dump(mode="json")
    a = pipeline.latest_analysis(db, c)
    r = reviews.latest_review(db, c.id)
    d["transcript"] = a.transcript if a else ""
    d["visual_notes"] = a.visual_notes if a else []
    d["scene_summary"] = (a.structured or {}).get("scene_summary") if a else None
    d["analysis_provider"] = f"{a.provider}/{a.model}" if a else None
    d["review"] = {"state": r.review_state, "reasons": r.reasons, "flags": r.flags, "policy_version": r.policy_version, "actor": r.actor} if r else None
    return d


@router.get("")
def inbox(db: Session = DB) -> dict:
    return {"items": [_detail(db, c.id) for c in reviews.inbox(db)]}


@router.get("/{clip_id}")
def detail(clip_id: str, db: Session = DB) -> dict:
    return _detail(db, clip_id)


@router.post("/{clip_id}/approve")
def approve(clip_id: str, body: NoteBody | None = None, db: Session = DB) -> dict:
    reviews.approve(db, clip_id, note=body.note if body else None)
    queue.rebuild(db)
    return _detail(db, clip_id)


@router.post("/{clip_id}/reject")
def reject(clip_id: str, body: NoteBody | None = None, db: Session = DB) -> dict:
    reviews.reject(db, clip_id, note=body.note if body else None)
    return _detail(db, clip_id)


@router.post("/{clip_id}/regenerate")
def regenerate(clip_id: str, db: Session = DB) -> dict:
    reviews.regenerate(db, clip_id)
    db.commit()
    runner.process_in_background()
    return _detail(db, clip_id)


@router.patch("/{clip_id}/content")
def content(clip_id: str, body: ContentBody, db: Session = DB) -> dict:
    reviews.edit_content(db, clip_id, overlay_title=body.overlay_title, caption=body.caption, hashtags=body.hashtags, approve_after=body.approve)
    db.commit()
    runner.process_in_background()
    return _detail(db, clip_id)
