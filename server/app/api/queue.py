from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import DB
from app.api.dto import pub_dto
from app.core.timeutil import iso
from app.domain import states as S
from app.services import publishing, queue
from app.services import settings_service as ss

router = APIRouter(prefix="/api/v1", tags=["queue"])


class QueuePatch(BaseModel):
    paused: bool | None = None
    caption: str | None = Field(None, max_length=5000)
    hashtags: list[str] | None = None
    swap_with: str | None = None
    due_at: datetime | None = None
    cancel: bool = False


@router.get("/queue")
def get_queue(db: Session = DB) -> dict:
    st = ss.get_settings(db)
    jobs = queue.list_jobs(db, [S.QUEUED_LOCAL, S.RETRY_PENDING, S.UPLOADING_ASSET, S.SCHEDULING, S.SCHEDULED_REMOTE, S.DRY_RUN_OK, S.PUB_NEEDS_REVIEW])
    return {"items": [pub_dto(j).model_dump(mode="json") for j in jobs], "daily_counts": queue.daily_counts(db), "daily_max": st.publishing.daily_max,
            "slots": st.publishing.slots, "timezone": st.publishing.timezone, "publish_mode": publishing.effective_mode(st), "now": iso(__import__("app.core.timeutil", fromlist=["utcnow"]).utcnow())}


@router.post("/queue/rebuild")
def rebuild(db: Session = DB) -> dict:
    return queue.rebuild(db)


@router.post("/queue/tick")
def tick(db: Session = DB) -> dict:
    return publishing.tick(db)


@router.patch("/queue/{job_id}")
def patch(job_id: str, body: QueuePatch, db: Session = DB) -> dict:
    j = queue.update_job(db, job_id, paused=body.paused, caption=body.caption, hashtags=body.hashtags, swap_with=body.swap_with, cancel=body.cancel, due_at=body.due_at)
    if body.cancel:
        queue.rebuild(db)  # ripianifica: gli slot liberati vengono riempiti
    return pub_dto(j).model_dump(mode="json")


@router.get("/publications")
def publications(status: str | None = None, limit: int = Query(100, ge=1, le=500), db: Session = DB) -> dict:
    jobs = queue.list_jobs(db, status.split(",") if status else None)
    jobs = sorted(jobs, key=lambda j: j.due_at, reverse=True)[:limit]
    return {"items": [pub_dto(j).model_dump(mode="json") for j in jobs]}


@router.post("/publications/{job_id}/reconcile")
def reconcile(job_id: str, db: Session = DB) -> dict:
    return pub_dto(publishing.reconcile_now(db, job_id)).model_dump(mode="json")


class VerifyBody(BaseModel):
    note: str = Field("", max_length=200)


@router.post("/publications/{job_id}/verify-external")
def verify_external(job_id: str, body: VerifyBody, db: Session = DB) -> dict:
    """L'utente conferma di aver visto il post pubblicato su TikTok (Buffer 'sent' non basta)."""
    return pub_dto(publishing.mark_externally_verified(db, job_id, body.note)).model_dump(mode="json")


@router.post("/publications/{job_id}/retry")
def retry(job_id: str, db: Session = DB) -> dict:
    return pub_dto(publishing.retry_job(db, job_id)).model_dump(mode="json")


@router.post("/publications/{job_id}/dispatch")
def dispatch(job_id: str, db: Session = DB) -> dict:
    """Esegue subito il job (dry-run/mock/live secondo la modalità attiva)."""
    publishing.process_job(db, job_id)
    db.expire_all()
    return pub_dto(queue.get_job(db, job_id)).model_dump(mode="json")
