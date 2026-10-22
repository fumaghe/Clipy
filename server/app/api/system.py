from __future__ import annotations

import shutil
import subprocess
from datetime import timedelta

from fastapi import APIRouter, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.adapters.ai.transcribe import asr_health
from app.adapters.twitch.mock import MockTwitch
from app.adapters.video.fixtures import FIXTURES
from app.api.deps import DB
from app.core.config import get_env
from app.core.errors import ClipyError
from app.core.timeutil import iso, utcnow
from app.db.models import Clip, Event, Lease, PublicationJob, SyncCursor
from app.domain import states as S
from app.services import publishing, queue
from app.services import settings_service as ss
from app.workers import runner

router = APIRouter(prefix="/api/v1/system", tags=["system"])


def _ver(binary: str) -> str | None:
    p = shutil.which(binary)
    if not p:
        return None
    try:
        return subprocess.run([binary, "-version"], capture_output=True, text=True, timeout=10).stdout.splitlines()[0]
    except Exception:
        return "installato"


@router.get("/status")
def status(db: Session = DB) -> dict:
    st = ss.get_settings(db)
    today = utcnow() - timedelta(hours=24)
    counts = dict(db.execute(select(Clip.status, func.count()).group_by(Clip.status)).all())
    lease = db.get(Lease, "worker")
    cur = db.get(SyncCursor, st.twitch.login.lstrip("@").lower())
    sec = ss.secrets_status(db)
    errors = db.scalar(select(func.count()).select_from(Event).where(Event.severity == "error", Event.at > today)) or 0
    published_today = db.scalar(select(func.count()).select_from(PublicationJob).where(PublicationJob.status == S.PUBLISHED, PublicationJob.due_at > today)) or 0
    mode = publishing.effective_mode(st)
    return {
        "app": "Clipy", "version": "1.0.0", "time": iso(utcnow()),
        "demo_mode": st.system.demo_mode,
        "publish_mode": mode,  # mock | dry_run | live
        "dry_run": mode != "live",
        "live_enabled": st.publishing.live_enabled,
        "collector_enabled": st.system.collector_enabled,
        "worker": {"running": runner.is_running(), "leader": bool(lease and lease.expires_at > utcnow()), "owner": lease.owner if lease else None},
        "twitch": {"login": st.twitch.login, "configured": sec["twitch_client_id"] and sec["twitch_client_secret"], "authorized": sec["twitch_access_token"],
                   "broadcaster_id": cur.broadcaster_id if cur else "", "last_scan_at": iso(cur.last_successful_scan_at) if cur else None,
                   "last_error": cur.last_error if cur else None, "simulated": st.system.demo_mode or not sec["twitch_client_id"]},
        "buffer": {"connected": st.publishing.is_connected, "channel_id": st.publishing.channel_id, "provider": st.publishing.provider},
        "storage": {"provider": st.storage.provider, "public": st.storage.provider == "s3"},
        "ffmpeg": _ver("ffmpeg"), "ffprobe": _ver("ffprobe"),
        "asr": asr_health(st.ai.whisper_model, st.ai.whisper_device),
        "counts": {
            "clips_total": sum(counts.values()), "new": counts.get(S.DISCOVERED, 0) + counts.get(S.WAITING_FOR_MEDIA, 0),
            "processing": sum(counts.get(x, 0) for x in (S.DOWNLOADED, S.ANALYZING, S.ANALYZED, S.RENDERING, S.READY)),
            "processed": counts.get(S.AUTO_READY, 0) + counts.get(S.APPROVED, 0) + counts.get(S.NEEDS_REVIEW, 0),
            "needs_review": counts.get(S.NEEDS_REVIEW, 0), "failed": counts.get(S.FAILED, 0),
            "queued": queue.count_status(db, S.QUEUED_LOCAL) + queue.count_status(db, S.RETRY_PENDING),
            "remote_scheduled": queue.count_status(db, S.SCHEDULED_REMOTE), "dry_run_ok": queue.count_status(db, S.DRY_RUN_OK),
            "published_today": published_today, "errors_24h": errors,
        },
        "next_slots": [iso(j.due_at) for j in queue.list_jobs(db, [S.QUEUED_LOCAL, S.RETRY_PENDING])[:4]],
        "daily_max": st.publishing.daily_max,
    }


@router.get("/events")
def events(limit: int = Query(50, ge=1, le=500), severity: str | None = None, db: Session = DB) -> list[dict]:
    q = select(Event).order_by(Event.id.desc()).limit(limit)
    if severity:
        q = q.where(Event.severity == severity)
    return [{"id": e.id, "at": iso(e.at), "component": e.component, "type": e.type, "severity": e.severity, "clip_id": e.clip_id, "job_id": e.job_id, "message": e.message}
            for e in db.scalars(q)]


@router.post("/collector/{action}")
def collector_toggle(action: str, db: Session = DB) -> dict:
    if action not in ("start", "stop"):
        raise ClipyError("Azione non valida", code="bad_action", status_code=422)
    ss.update_settings(db, {"system": {"collector_enabled": action == "start"}})
    from app.services import events as ev

    ev.emit(db, "system", f"collector_{action}", f"Collector {'avviato' if action == 'start' else 'fermato'}")
    return {"collector_enabled": action == "start"}


@router.post("/process")
def process() -> dict:
    return {"started": runner.process_in_background()}


@router.post("/demo/spawn")
def demo_spawn(fixture: str | None = None, db: Session = DB) -> dict:
    st = ss.get_settings(db)
    if not st.system.demo_mode:
        raise ClipyError("Disponibile solo in modalità demo", code="not_demo", status_code=409)
    if fixture and fixture not in FIXTURES:
        raise ClipyError("Fixture sconosciuta", code="bad_fixture", status_code=422)
    m = MockTwitch().spawn(fixture)
    return {"clip_id": m.id, "simulated": True, "scenario": m.extra["scenario"]}


@router.get("/demo/fixtures")
def demo_fixtures() -> list[dict]:
    return [{"key": f.key, "title": f.title, "scenario": f.scenario, "portrait": f.portrait_on_twitch} for f in FIXTURES.values()]


@router.get("/health-detail")
def health_detail() -> dict:
    return {"data_dir": str(get_env().data_dir), "db": get_env().db_url.split("///")[0]}
