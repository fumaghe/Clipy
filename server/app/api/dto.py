from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, field_serializer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.timeutil import iso
from app.db.models import Clip, PublicationJob, Review
from app.domain import states as S
from app.services import pipeline


class _Base(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    @field_serializer("*", when_used="json", check_fields=False)
    def _dt(self, v: Any):
        return iso(v) if isinstance(v, datetime) else v


class PaginatedResponse(BaseModel):
    items: list[Any]
    total: int
    page: int
    page_size: int


class ReviewDTO(_Base):
    id: str
    clip_id: str
    policy_version: str
    review_state: str
    reasons: list[Any]
    flags: list[str]
    human_override: bool
    actor: str
    note: str | None
    changes: dict[str, Any]
    reviewed_at: datetime | None
    created_at: datetime


class AnalysisDTO(_Base):
    id: str
    transcript: str
    segments: list[Any]
    language: str
    asr_engine: str
    asr_available: bool
    visual_notes: list[Any]
    structured: dict[str, Any]
    confidence: float
    quality_flags: list[str]
    prompt_version: str
    provider: str
    model: str
    simulated: bool
    tokens_in: int
    tokens_out: int
    cost_usd: float
    created_at: datetime


class PublicationDTO(_Base):
    id: str
    clip_id: str
    platform: str
    provider: str
    target_channel_id: str
    caption: str
    hashtags: list[str]
    due_at: datetime
    status: str
    paused: bool
    position: int
    simulated: bool
    remote_post_id: str | None
    remote_status: str | None
    remote_url: str | None
    attempts: int
    next_attempt_at: datetime | None
    error: str | None
    last_reconciled_at: datetime | None
    remote_state: str = "LOCAL"
    verification: str = "NONE"
    verified_at: datetime | None = None
    created_at: datetime
    clip_title: str | None = None
    overlay_title: str | None = None


class ClipDTO(_Base):
    id: str
    source_clip_id: str
    creator_name: str
    broadcaster_name: str
    game_name: str
    twitch_url: str
    title: str
    created_at_utc: datetime
    discovered_at_utc: datetime
    duration: float
    status: str
    error: str | None
    is_demo: bool
    demo_scenario: str
    portrait_available: bool | None
    is_portrait_file: bool
    landscape_fallback: bool
    overlay_title: str | None
    caption: str | None
    hashtags: list[str]
    template_id: str | None
    content_version: int
    paused: bool
    priority: int
    has_original: bool = False
    has_render: bool = False
    transcript_excerpt: str = ""
    confidence: float | None = None
    analysis_simulated: bool = False
    review_state: str | None = None
    review_reasons: list[Any] = []
    publication_status: str | None = None
    publication_due_at: datetime | None = None
    busy: bool = False  # analisi/render/lock attivi: i comandi che modificano la clip rispondono 409 `busy`
    busy_reason: str | None = None
    pending: bool = False  # in attesa nella pipeline automatica (download/analisi/render non ancora conclusi)


def clip_dto(db: Session, c: Clip) -> ClipDTO:
    from pathlib import Path

    a = pipeline.latest_analysis(db, c)
    r = db.scalar(select(Review).where(Review.clip_id == c.id).order_by(Review.created_at.desc(), Review.id.desc()))
    j = db.scalar(select(PublicationJob).where(PublicationJob.clip_id == c.id, PublicationJob.status != S.CANCELLED).order_by(PublicationJob.created_at.desc()))
    d = ClipDTO.model_validate(c)
    d.has_original = bool(c.original_path and Path(c.original_path).exists())
    d.has_render = bool(c.render_path and Path(c.render_path).exists())
    d.transcript_excerpt = (a.transcript[:160] if a else "")
    d.confidence = a.confidence if a else None
    d.analysis_simulated = bool(a and a.simulated)
    d.review_state = r.review_state if r else None
    d.review_reasons = r.reasons if r else []
    d.publication_status = j.status if j else None
    d.publication_due_at = j.due_at if j else None
    d.busy_reason = pipeline.busy_reason(c)
    d.busy = d.busy_reason is not None
    d.pending = c.status in S.CLIP_ACTIVE_PIPELINE
    return d


def pub_dto(j: PublicationJob) -> PublicationDTO:
    d = PublicationDTO.model_validate(j)
    d.clip_title = j.clip.title
    d.overlay_title = j.clip.overlay_title
    return d
