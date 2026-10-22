from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.timeutil import utcnow
from app.db.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class Clip(Base):
    __tablename__ = "clips"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    source_clip_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    broadcaster_id: Mapped[str] = mapped_column(String(64), default="")
    broadcaster_name: Mapped[str] = mapped_column(String(128), default="")
    creator_id: Mapped[str] = mapped_column(String(64), default="")
    creator_name: Mapped[str] = mapped_column(String(128), default="")
    game_id: Mapped[str] = mapped_column(String(64), default="")
    game_name: Mapped[str] = mapped_column(String(256), default="")
    twitch_url: Mapped[str] = mapped_column(String(512), default="")
    title: Mapped[str] = mapped_column(String(512), default="")
    created_at_utc: Mapped[datetime] = mapped_column(DateTime, index=True)
    discovered_at_utc: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    duration: Mapped[float] = mapped_column(Float, default=0.0)
    view_count: Mapped[int] = mapped_column(Integer, default=0)
    thumbnail_url: Mapped[str] = mapped_column(String(512), default="")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    demo_scenario: Mapped[str] = mapped_column(String(32), default="")  # SAFE / AMBIGUOUS / BLOCKED
    # media
    portrait_available: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_portrait_file: Mapped[bool] = mapped_column(Boolean, default=False)
    landscape_fallback: Mapped[bool] = mapped_column(Boolean, default=False)
    original_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    media_info: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    download_attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    waiting_since: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # pipeline
    status: Mapped[str] = mapped_column(String(32), default="DISCOVERED", index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    step_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    # contenuto finale (editabile)
    overlay_title: Mapped[str | None] = mapped_column(String(256), nullable=True)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    hashtags: Mapped[list[str]] = mapped_column(JSON, default=list)
    template_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    render_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    render_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content_version: Mapped[int] = mapped_column(Integer, default=1)
    approve_after_render: Mapped[bool] = mapped_column(Boolean, default=False)
    human_edited: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    analyses: Mapped[list[Analysis]] = relationship(back_populates="clip", cascade="all, delete-orphan")


class Analysis(Base):
    __tablename__ = "analyses"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    clip_id: Mapped[str] = mapped_column(ForeignKey("clips.id", ondelete="CASCADE"), index=True)
    transcript: Mapped[str] = mapped_column(Text, default="")
    segments: Mapped[list[Any]] = mapped_column(JSON, default=list)
    language: Mapped[str] = mapped_column(String(16), default="it")
    asr_engine: Mapped[str] = mapped_column(String(64), default="")
    asr_available: Mapped[bool] = mapped_column(Boolean, default=True)
    visual_notes: Mapped[list[Any]] = mapped_column(JSON, default=list)
    structured: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    quality_flags: Mapped[list[str]] = mapped_column(JSON, default=list)
    prompt_version: Mapped[str] = mapped_column(String(32), default="")
    provider: Mapped[str] = mapped_column(String(32), default="")
    model: Mapped[str] = mapped_column(String(128), default="")
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    timings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    clip: Mapped[Clip] = relationship(back_populates="analyses")


class Review(Base):
    __tablename__ = "reviews"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    clip_id: Mapped[str] = mapped_column(ForeignKey("clips.id", ondelete="CASCADE"), index=True)
    policy_version: Mapped[str] = mapped_column(String(32))
    review_state: Mapped[str] = mapped_column(String(24), index=True)  # AUTO_READY|NEEDS_REVIEW|APPROVED|REJECTED
    reasons: Mapped[list[Any]] = mapped_column(JSON, default=list)
    flags: Mapped[list[str]] = mapped_column(JSON, default=list)
    human_override: Mapped[bool] = mapped_column(Boolean, default=False)
    actor: Mapped[str] = mapped_column(String(64), default="system")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    changes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Template(Base):
    __tablename__ = "templates"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    version: Mapped[int] = mapped_column(Integer, default=1)
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    reference_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class RenderJob(Base):
    __tablename__ = "render_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    clip_id: Mapped[str] = mapped_column(ForeignKey("clips.id", ondelete="CASCADE"), index=True)
    template_id: Mapped[str] = mapped_column(String(64))
    template_version: Mapped[int] = mapped_column(Integer, default=1)
    reference_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    fingerprint: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(16), default="RUNNING")  # RUNNING|DONE|FAILED
    output_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    checks: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class PublicationJob(Base):
    __tablename__ = "publication_jobs"
    __table_args__ = (UniqueConstraint("clip_id", "target_channel_id", "attempt_version", name="uq_pub_clip_channel_ver"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    clip_id: Mapped[str] = mapped_column(ForeignKey("clips.id", ondelete="CASCADE"), index=True)
    platform: Mapped[str] = mapped_column(String(24), default="tiktok")
    provider: Mapped[str] = mapped_column(String(24), default="buffer")
    target_channel_id: Mapped[str] = mapped_column(String(128), default="default")
    attempt_version: Mapped[int] = mapped_column(Integer, default=1)
    caption: Mapped[str] = mapped_column(Text, default="")
    hashtags: Mapped[list[str]] = mapped_column(JSON, default=list)
    due_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    status: Mapped[str] = mapped_column(String(24), default="QUEUED_LOCAL", index=True)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    position: Mapped[int] = mapped_column(Integer, default=0)
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    asset_key: Mapped[str | None] = mapped_column(String(256), nullable=True)
    asset_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    remote_post_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    remote_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    remote_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content_version: Mapped[int] = mapped_column(Integer, default=1)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_reconciled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    clip: Mapped[Clip] = relationship()


class StateTransition(Base):
    __tablename__ = "state_transitions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity: Mapped[str] = mapped_column(String(24))
    entity_id: Mapped[str] = mapped_column(String(36), index=True)
    from_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_state: Mapped[str] = mapped_column(String(32))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Secret(Base):
    __tablename__ = "secrets"
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class SyncCursor(Base):
    __tablename__ = "sync_cursors"
    broadcaster_login: Mapped[str] = mapped_column(String(64), primary_key=True)
    broadcaster_id: Mapped[str] = mapped_column(String(64), default="")
    last_successful_scan_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_window_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_scan_found: Mapped[int] = mapped_column(Integer, default=0)
    last_scan_new: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (Index("ix_events_at", "at"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    component: Mapped[str] = mapped_column(String(32))
    type: Mapped[str] = mapped_column(String(48))
    severity: Mapped[str] = mapped_column(String(12), default="info")
    clip_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    message: Mapped[str] = mapped_column(Text)


class Lease(Base):
    __tablename__ = "leases"
    name: Mapped[str] = mapped_column(String(48), primary_key=True)
    owner: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime)


class OAuthState(Base):
    __tablename__ = "oauth_states"
    state: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
