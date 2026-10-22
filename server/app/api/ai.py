from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.adapters.ai.base import Transcript
from app.api.deps import DB
from app.services import factory, pipeline, reviews
from app.services import settings_service as ss

router = APIRouter(prefix="/api/v1/ai", tags=["ai"])


class AltBody(BaseModel):
    clip_id: str
    count: int = 3


@router.post("/alternatives")
def alternatives(body: AltBody, db: Session = DB) -> dict:
    """Genera alternative di titolo SENZA salvarle né ripetere download/ASR."""
    c = reviews.get_clip(db, body.clip_id)
    last = pipeline.latest_analysis(db, c)
    st = ss.get_settings(db)
    tr = Transcript(last.transcript, last.segments, last.language, last.asr_engine, last.asr_available, last.simulated) if last else Transcript("", [], "", "", False)
    an = factory.analyzer(db, st, c.is_demo)
    out, avoid, cost = [], [c.overlay_title or ""], 0.0
    for i in range(max(1, min(body.count, 5))):
        res = an.analyze(pipeline.build_context(db, c, tr, [], variation=i + 1, avoid=[x for x in avoid if x]))
        out.append({"overlay_title": res.output.overlay_title, "caption": res.output.tiktok_caption, "hashtags": res.output.hashtags, "simulated": res.simulated})
        avoid.append(res.output.overlay_title)
        cost += res.cost_usd
    return {"items": out, "estimated_cost_usd": round(cost, 5)}


@router.get("/prompt-preview")
def prompt_preview(db: Session = DB) -> dict:
    from app.adapters.ai.base import AnalysisContext
    from app.adapters.ai.prompts import PROMPT_VERSION, build_system

    ai = ss.get_settings(db).ai
    ctx = AnalysisContext("", "", Transcript("", []), language=ai.language, tone=ai.tone, prompt_extra=ai.prompt_extra, banned_words=ai.banned_words,
                          max_emoji=ai.max_emoji, max_hashtags=ai.max_hashtags, max_overlay_chars=ai.max_overlay_chars)
    return {"version": PROMPT_VERSION, "system": build_system(ctx)}


@router.get("/costs")
def costs(db: Session = DB) -> dict:
    from sqlalchemy import func, select

    from app.db.models import Analysis

    n, tin, tout, cost = db.execute(select(func.count(), func.coalesce(func.sum(Analysis.tokens_in), 0), func.coalesce(func.sum(Analysis.tokens_out), 0), func.coalesce(func.sum(Analysis.cost_usd), 0.0))).one()
    return {"analyses": n, "tokens_in": tin, "tokens_out": tout, "cost_usd_estimate": round(cost, 4)}
