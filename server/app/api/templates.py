from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.video.fixtures import ensure_fixture
from app.adapters.video.renderer import FFmpegRenderer, resolve_font
from app.api.deps import DB
from app.core.config import get_env
from app.core.errors import ClipyError
from app.db.models import Clip, Template
from app.domain import states as S
from app.services import events, pipeline, reviews
from app.services import settings_service as ss
from app.services.templates import TemplateConfig, get_template, validate_config
from app.workers import runner

router = APIRouter(prefix="/api/v1/templates", tags=["templates"])


class TemplateBody(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    config: dict
    reference_verified: bool | None = None


class PreviewBody(BaseModel):
    config: dict | None = None
    text: str = Field("Titolo di esempio", max_length=300)
    clip_id: str | None = None
    at_seconds: float | None = Field(None, ge=0)


class ApplyBody(BaseModel):
    clip_ids: list[str] | None = None


def _dto(t: Template, active_id: str) -> dict:
    return {"id": t.id, "name": t.name, "version": t.version, "active": t.id == active_id, "reference_verified": t.reference_verified, "config": t.config,
            "reference_status": "VERIFIED" if t.reference_verified else "REFERENCE_NOT_VERIFIED"}


@router.get("")
def list_templates(db: Session = DB) -> dict:
    active = ss.get_settings(db).render.active_template_id
    items = [_dto(t, active) for t in db.scalars(select(Template).order_by(Template.name))]
    fonts, warns = resolve_font(TemplateConfig().font_file)
    return {"items": items, "active_template_id": active, "font_resolved": str(fonts) if fonts else None, "font_warnings": warns}


@router.get("/{template_id}")
def get_(template_id: str, db: Session = DB) -> dict:
    return _dto(get_template(db, template_id), ss.get_settings(db).render.active_template_id)


@router.post("")
def create(body: TemplateBody, db: Session = DB) -> dict:
    import uuid

    cfg = validate_config(body.config)
    t = Template(id=uuid.uuid4().hex[:10], name=body.name, config=cfg.model_dump(), reference_verified=bool(body.reference_verified))
    db.add(t)
    db.flush()
    return _dto(t, ss.get_settings(db).render.active_template_id)


@router.put("/{template_id}")
def update(template_id: str, body: TemplateBody, db: Session = DB) -> dict:
    t = get_template(db, template_id)
    t.config = validate_config(body.config).model_dump()
    t.name = body.name
    t.version += 1
    if body.reference_verified is not None:
        t.reference_verified = body.reference_verified
    db.flush()
    return _dto(t, ss.get_settings(db).render.active_template_id)


@router.post("/{template_id}/activate")
def activate(template_id: str, db: Session = DB) -> dict:
    get_template(db, template_id)
    ss.update_settings(db, {"render": {"active_template_id": template_id}})
    return {"active_template_id": template_id}


@router.post("/{template_id}/apply")
def apply(template_id: str, body: ApplyBody, db: Session = DB) -> dict:
    """Ri-renderizza le clip non ancora pianificate remotamente con il template (invalida solo il render)."""
    get_template(db, template_id)
    q = select(Clip).where(Clip.status.in_([S.AUTO_READY, S.APPROVED, S.NEEDS_REVIEW]))
    if body.clip_ids:
        q = q.where(Clip.id.in_(body.clip_ids))
    n = 0
    for c in db.scalars(q):
        if c.template_id not in (None, template_id) and not body.clip_ids:
            continue
        if c.status == S.APPROVED:
            c.approve_after_render, c.human_edited = True, True
        pipeline.invalidate_render(db, c)
        n += 1
    events.emit(db, "renderer", "template_apply", f"Template {template_id} applicato: {n} clip da ri-renderizzare")
    db.commit()
    runner.process_in_background()
    return {"rerender": n}


@router.post("/{template_id}/preview")
def preview(template_id: str, body: PreviewBody, db: Session = DB):
    """Preview REALE: stesso FFmpegRenderer/filtergraph del render finale."""
    t = get_template(db, template_id) if template_id != "draft" else None
    cfg = validate_config(body.config) if body.config else TemplateConfig.model_validate(t.config if t else {})
    if body.clip_id:
        c = reviews.get_clip(db, body.clip_id)
        src = Path(c.original_path or "")
        if not src.exists():
            raise ClipyError("Originale non disponibile per l'anteprima", code="no_original", status_code=409)
        key = c.id
    else:
        src, key = ensure_fixture("skill_landscape"), "fixture-landscape"
    st = ss.get_settings(db)
    r = FFmpegRenderer(st.render.fps)
    out = get_env().dir("previews") / f"tpl-{key}-{abs(hash((body.text, body.at_seconds, cfg.model_dump_json()))) % 10**10}.png"
    r.preview_still(src, body.text, cfg, out, body.at_seconds)
    return FileResponse(out, media_type="image/png", headers={"Cache-Control": "no-store"})


@router.post("/{template_id}/reference")
async def upload_reference(template_id: str, file: UploadFile = File(...), db: Session = DB) -> dict:
    """Importa screenshot/video di riferimento. NON imposta reference_verified: la conferma resta all'utente."""
    get_template(db, template_id)
    ext = Path(file.filename or "ref.bin").suffix.lower()
    if ext not in (".png", ".jpg", ".jpeg", ".mp4", ".mov", ".webm"):
        raise ClipyError("Formato non supportato (png/jpg/mp4/mov/webm)", code="bad_ref_format", status_code=422)
    dest = get_env().dir("reference") / f"{template_id}{ext}"
    data = await file.read()
    if len(data) > 100 * 1024 * 1024:
        raise ClipyError("File oltre 100 MB", code="too_large", status_code=413)
    dest.write_bytes(data)
    events.emit(db, "renderer", "reference_imported", f"Riferimento importato per template {template_id} (non ancora verificato)")
    return {"saved": dest.name, "reference_verified": False}
