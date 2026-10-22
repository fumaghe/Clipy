from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, Field, field_validator


@dataclass
class Transcript:
    text: str
    segments: list[dict] = field(default_factory=list)
    language: str = ""
    engine: str = ""
    available: bool = True  # False = ASR non disponibile (≠ clip muta)
    simulated: bool = False
    note: str = ""


class Transcriber(Protocol):
    def transcribe(self, video: Path, language: str | None) -> Transcript: ...


class AnalysisOutput(BaseModel):
    language: str = "it"
    scene_summary: str = ""
    overlay_title: str = ""
    tiktok_caption: str = ""
    hashtags: list[str] = Field(default_factory=list)
    content_category: Literal["funny", "skill", "fail", "reaction", "other"] = "other"
    confidence: float = Field(0.0, ge=0, le=1)
    requires_review: bool = False
    reason: str | None = None
    quality_flags: list[str] = Field(default_factory=list)

    @field_validator("hashtags", mode="before")
    @classmethod
    def _tags(cls, v):
        return [str(x) for x in v] if isinstance(v, list) else []


@dataclass
class AnalysisContext:
    clip_title: str
    game_name: str
    transcript: Transcript
    frames: list[Path] = field(default_factory=list)
    visual_notes: list[str] = field(default_factory=list)
    duration: float = 0.0
    language: str = "it"
    tone: str = "ironic_meme"
    prompt_extra: str = ""
    banned_words: list[str] = field(default_factory=list)
    max_emoji: int = 1
    max_hashtags: int = 5
    max_overlay_chars: int = 60
    max_caption_chars: int = 220
    avoid_titles: list[str] = field(default_factory=list)
    demo_fixture: str | None = None
    variation: int = 0  # rigenerazione: alternative diverse
    budget_usd: float = 0.0  # 0 = nessun limite


@dataclass
class AnalysisResult:
    output: AnalysisOutput
    provider: str
    model: str
    prompt_version: str
    simulated: bool = False
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    timings: dict = field(default_factory=dict)


class ContentAnalyzer(Protocol):
    def analyze(self, ctx: AnalysisContext) -> AnalysisResult: ...
