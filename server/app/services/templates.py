from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from app.core.config import get_env
from app.core.errors import ClipyError, NotFound
from app.db.models import Template


class OverlayDuration(BaseModel):
    mode: Literal["full", "first_seconds", "time_range"] = "full"
    seconds: float = Field(3.0, ge=0.5, le=120)
    start: float = Field(0.0, ge=0)
    end: float = Field(3.0, gt=0)

    @model_validator(mode="after")
    def _chk(self):
        if self.mode == "time_range" and self.end <= self.start:
            raise ValueError("time_range: end deve essere > start")
        return self


class TemplateConfig(BaseModel):
    canvas_width: int = Field(1080, ge=360, le=2160)
    canvas_height: int = Field(1920, ge=640, le=3840)
    landscape_mode: Literal["crop", "letterbox"] = "crop"
    crop_offset_x: float = Field(0.0, ge=-1, le=1)  # -1 sinistra, 0 centro, 1 destra
    crop_offset_y: float = Field(0.0, ge=-1, le=1)
    font_file: str = "DejaVuSans-Bold.ttf"
    font_size: int = Field(88, ge=24, le=240)
    min_font_size: int = Field(44, ge=16, le=240)
    text_color: str = "#FFFFFF"
    stroke_color: str = "#000000"
    stroke_width: int = Field(6, ge=0, le=24)
    shadow: bool = True
    shadow_color: str = "#000000"
    shadow_offset: int = Field(4, ge=0, le=20)
    background_box: bool = False
    box_color: str = "#000000"
    box_opacity: float = Field(0.45, ge=0, le=1)
    align: Literal["center", "left", "right"] = "center"
    max_lines: int = Field(3, ge=1, le=6)
    max_width_percent: float = Field(86, ge=30, le=100)
    y_percent: float = Field(50, ge=5, le=95)
    vertical_offset: int = Field(0, ge=-600, le=600)
    safe_margin_percent: float = Field(6, ge=0, le=20)
    duration: OverlayDuration = Field(default_factory=OverlayDuration)
    fade_in: float = Field(0.25, ge=0, le=3)
    fade_out: float = Field(0.25, ge=0, le=3)
    uppercase: bool = False
    blur_background: bool = True  # letterbox: sfondo sfocato


DEFAULT_CONFIG = TemplateConfig()


def seed_templates(db: Session) -> None:
    if db.get(Template, "default") is None:
        path = get_env().templates_dir / "default.json"
        cfg = TemplateConfig.model_validate(json.loads(path.read_text("utf-8"))) if path.exists() else DEFAULT_CONFIG
        db.add(Template(id="default", name="Neutro (preset di partenza)", version=1, active=True,
                        reference_verified=False, config=cfg.model_dump()))
        db.flush()


def get_template(db: Session, template_id: str) -> Template:
    t = db.get(Template, template_id)
    if t is None:
        raise NotFound(f"Template {template_id} non trovato", code="template_not_found")
    return t


def validate_config(raw: dict[str, Any]) -> TemplateConfig:
    try:
        return TemplateConfig.model_validate(raw)
    except Exception as exc:
        raise ClipyError(f"Template non valido: {exc}", code="invalid_template", status_code=422) from exc
