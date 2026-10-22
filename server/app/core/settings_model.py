"""Impostazioni di prodotto (senza segreti). Validate con Pydantic, persistite nel DB (versionate)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class TwitchSettings(BaseModel):
    login: str = "iDensTv"
    collect_all_channel_clips: bool = True
    polling_minutes: int = Field(5, ge=1, le=120)
    bootstrap_hours: int = Field(24, ge=1, le=24 * 30)
    overlap_hours: int = Field(24, ge=1, le=24 * 7)
    prefer_portrait: bool = True
    landscape_fallback: Literal["auto_crop", "letterbox", "review"] = "auto_crop"
    portrait_wait_minutes: int = Field(10, ge=0, le=240)
    max_download_mb: int = Field(300, ge=5, le=2000)


class AISettings(BaseModel):
    transcription: Literal["faster-whisper-local", "off"] = "faster-whisper-local"
    whisper_model: str = "small"
    whisper_device: Literal["auto", "cpu", "cuda"] = "auto"
    provider: Literal["mock", "anthropic", "openai"] = "mock"
    model: str = ""
    base_url: str = ""
    language: str = "it"
    tone: Literal["ironic_meme", "clickbait_safe", "competitive", "descriptive"] = "ironic_meme"
    prompt_extra: str = ""
    banned_words: list[str] = Field(default_factory=lambda: ["suicidio", "ammazzati", "nazi"])
    max_emoji: int = Field(1, ge=0, le=5)
    max_hashtags: int = Field(5, ge=0, le=5)
    vision_enabled: bool = False
    max_frames: int = Field(6, ge=1, le=16)
    frame_interval_seconds: int = Field(4, ge=1, le=15)
    budget_usd_per_clip: float = Field(0.05, ge=0, le=5)
    max_overlay_chars: int = Field(60, ge=10, le=120)


class ReviewSettings(BaseModel):
    preset: Literal["prudent", "balanced", "permissive"] = "prudent"
    min_confidence: float = Field(0.6, ge=0, le=1)
    require_evidence: bool = True
    review_landscape_crop: bool = False
    review_if_asr_unavailable: bool = False
    detect_pii: bool = True


class PublishingSettings(BaseModel):
    provider: Literal["buffer", "mock"] = "buffer"
    is_connected: bool = False
    channel_id: str = ""
    policy: Literal["auto_with_review_gate"] = "auto_with_review_gate"
    live_enabled: bool = False
    dry_run: bool = True
    daily_max: int = Field(4, ge=1, le=25)
    timezone: str = "Europe/Rome"
    slots: list[str] = Field(default_factory=lambda: ["10:00", "13:30", "17:30", "21:00"])
    remote_max_scheduled: int = Field(10, ge=1, le=100)
    lookahead_hours: int = Field(72, ge=1, le=24 * 14)
    max_caption_length: int = Field(2200, ge=50, le=5000)
    media_cleanup_hours: int = Field(24, ge=1, le=24 * 30)
    max_attempts: int = Field(5, ge=1, le=20)

    @field_validator("slots")
    @classmethod
    def _slots(cls, v: list[str]) -> list[str]:
        out = []
        for s in v:
            h, _, m = s.partition(":")
            if not (h.isdigit() and m.isdigit() and 0 <= int(h) < 24 and 0 <= int(m) < 60):
                raise ValueError(f"slot non valido: {s}")
            out.append(f"{int(h):02d}:{int(m):02d}")
        return sorted(set(out))

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str) -> str:
        from zoneinfo import ZoneInfo

        ZoneInfo(v)
        return v


class StorageSettings(BaseModel):
    provider: Literal["local", "s3"] = "local"
    bucket: str = ""
    endpoint_url: str = ""
    region: str = "auto"
    public_base_url: str = ""
    key_prefix: str = "clipy/"


class RenderSettings(BaseModel):
    active_template_id: str = "default"
    reference_tiktok_url: str = "https://vm.tiktok.com/ZN8Ap3hEK/"
    fps: int = Field(30, ge=23, le=60)
    video_bitrate_k: int = Field(6000, ge=1000, le=20000)
    audio_bitrate_k: int = Field(128, ge=64, le=128)
    max_duration_seconds: int = Field(180, ge=5, le=600)
    preset: Literal["ultrafast", "veryfast", "fast", "medium"] = "veryfast"


class SystemSettings(BaseModel):
    collector_enabled: bool = False
    deployment_mode: str = "local_first_cloud_ready"
    retention_days: int = Field(30, ge=1, le=3650)
    demo_mode: bool = True


class ProductSettings(BaseModel):
    twitch: TwitchSettings = Field(default_factory=TwitchSettings)
    ai: AISettings = Field(default_factory=AISettings)
    review: ReviewSettings = Field(default_factory=ReviewSettings)
    publishing: PublishingSettings = Field(default_factory=PublishingSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    render: RenderSettings = Field(default_factory=RenderSettings)
    system: SystemSettings = Field(default_factory=SystemSettings)


SECRET_NAMES = {
    "twitch_client_id",
    "twitch_client_secret",
    "buffer_api_key",
    "llm_api_key",
    "s3_access_key_id",
    "s3_secret_access_key",
    "twitch_access_token",
    "twitch_refresh_token",
}
