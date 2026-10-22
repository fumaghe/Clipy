"""Dispatch verso il Publisher, retry con backoff, riconciliazione remota senza doppioni."""
from __future__ import annotations

import hashlib
import random
import uuid
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.publisher.base import PublishRequest
from app.core.errors import ClipyError, TemporaryError
from app.core.settings_model import ProductSettings
from app.core.timeutil import utcnow
from app.db.models import PublicationJob
from app.domain import captions
from app.domain import states as S
from app.services import events, factory, leases, queue
from app.services import settings_service as ss


def backoff(attempts: int) -> timedelta:
    return timedelta(seconds=min(30 * 2 ** attempts, 3600) * random.uniform(0.75, 1.25))  # esponenziale + jitter


def effective_mode(st: ProductSettings) -> str:
    """mock | dry_run | live."""
    p = st.publishing
    if p.provider == "mock":
        return "mock"
    if p.dry_run or not p.live_enabled or not p.is_connected:
        return "dry_run"
    return "live"


def _fingerprint(job: PublicationJob, text: str) -> str:
    return hashlib.sha256(f"{job.clip_id}|{job.attempt_version}|{job.target_channel_id}|{job.due_at.isoformat()}|{text}".encode()).hexdigest()[:32]


def _fail(db: Session, job: PublicationJob, exc: ClipyError, st: ProductSettings) -> None:
    job.error = exc.message
    job.attempts += 1
    if isinstance(exc, TemporaryError) and job.attempts < st.publishing.max_attempts:
        job.next_attempt_at = utcnow() + backoff(job.attempts)
        events.transition(db, "publication", job, S.RETRY_PENDING, exc.message)
    elif exc.code == "buffer_mutation_error":
        events.transition(db, "publication", job, S.PUB_NEEDS_REVIEW, exc.message)  # es. richiede passaggio TikTok manuale
    else:
        events.transition(db, "publication", job, S.PUB_FAILED, exc.message)
    events.emit(db, "publisher", "publish_error", exc.message, severity="warning", job_id=job.id, clip_id=job.clip_id)


def process_job(db: Session, job_id: str) -> None:
    if not leases.lock_row(db, PublicationJob, job_id, 600):
        return
    try:
        job = db.get(PublicationJob, job_id)
        assert job is not None
        _process(db, job)
        db.commit()
    except Exception as exc:
        db.rollback()
        job = db.get(PublicationJob, job_id)
        if job:
            events.emit(db, "publisher", "unexpected_error", str(exc), severity="error", job_id=job_id)
            job.error = f"Errore inatteso: {exc}"[:300]
            job.next_attempt_at = utcnow() + backoff(job.attempts + 1)
            db.commit()
    finally:
        leases.unlock_row(db, PublicationJob, job_id)


def _process(db: Session, job: PublicationJob) -> None:
    st = ss.get_settings(db)
    clip = job.clip
    if clip.status not in S.CLIP_ELIGIBLE or not clip.render_path or not Path(clip.render_path).exists():
        events.transition(db, "publication", job, S.CANCELLED, "clip non più idonea o render mancante")
        return
    text = captions.compose_post_text(job.caption or clip.caption or "", job.hashtags or clip.hashtags or [], st.publishing.max_caption_length)
    mode = effective_mode(st)
    job.fingerprint = _fingerprint(job, text)
    if mode == "dry_run":
        # zero richieste esterne: né upload, né create
        job.simulated, job.error = True, None
        job.remote_status = "dry_run"
        events.transition(db, "publication", job, S.DRY_RUN_OK, "DRY RUN: nessuna richiesta esterna")
        events.emit(db, "publisher", "dry_run", f"[DRY RUN] Simulata pubblicazione di {clip.source_clip_id} per {job.due_at.isoformat()}Z", job_id=job.id, clip_id=clip.id)
        return
    try:
        publisher = factory.publisher(db, st)
        storage = factory.storage(db, st, for_live=mode == "live")
        if not job.asset_url:
            events.transition(db, "publication", job, S.UPLOADING_ASSET)
            db.commit()
            key = f"{uuid.uuid4().hex}.mp4"  # nome opaco/unguessable
            asset = storage.upload(Path(clip.render_path), key)
            job.asset_key, job.asset_url = asset.key, asset.url
            if mode == "live":
                ok, why = storage.verify_public(asset.url)
                if not ok:
                    job.asset_url = None
                    raise TemporaryError(f"Media non raggiungibile pubblicamente: {why}", code="media_unreachable")
            db.commit()
        # persistiamo l'intenzione PRIMA della create: dopo un crash si riconcilia, non si rilancia alla cieca
        events.transition(db, "publication", job, S.SCHEDULING)
        job.next_attempt_at = None
        db.commit()
        if mode == "live":
            remote_count = publisher.count_scheduled(job.target_channel_id)
            if remote_count >= st.publishing.remote_max_scheduled:
                events.transition(db, "publication", job, S.QUEUED_LOCAL, "coda remota piena: resta in coda locale")
                return
        remote = publisher.create_post(PublishRequest(job.target_channel_id, text, job.asset_url or "", job.due_at, job.fingerprint))
        job.remote_post_id, job.remote_status, job.remote_url = remote.id, remote.status, remote.url
        job.simulated = bool(getattr(publisher, "simulated", False))
        job.error = None
        events.transition(db, "publication", job, S.SCHEDULED_REMOTE, "pianificato (NON ancora pubblicato)")
        events.emit(db, "publisher", "scheduled", f"Pianificato su {publisher.name} ({remote.id}){' [SIMULATO]' if job.simulated else ''}", job_id=job.id, clip_id=clip.id)
    except TemporaryError as exc:
        if job.status == S.SCHEDULING and exc.code in ("buffer_timeout", "buffer_unreachable"):
            # esito sconosciuto: NON ritentare la create alla cieca → recovery con ricerca remota
            job.error = exc.message
            job.next_attempt_at = utcnow() + timedelta(seconds=120)
            events.emit(db, "publisher", "create_ambiguous", "Esito della create sconosciuto: verrà riconciliato prima di ogni retry", severity="warning", job_id=job.id)
        else:
            _fail(db, job, exc, st)
    except ClipyError as exc:
        _fail(db, job, exc, st)


def recover_scheduling(db: Session, job: PublicationJob) -> None:
    """Job rimasto in SCHEDULING senza ID remoto (crash/timeout): cerca il post remoto prima di ritentare."""
    st = ss.get_settings(db)
    try:
        publisher = factory.publisher(db, st)
        found = publisher.find_posts(job.target_channel_id, job.due_at)
    except ClipyError as exc:
        job.error = exc.message
        job.next_attempt_at = utcnow() + backoff(job.attempts + 1)
        return
    text = captions.compose_post_text(job.caption, job.hashtags, st.publishing.max_caption_length)
    matching = [p for p in found if p.text.strip() == text.strip()] or found
    if len(matching) == 1:
        p = matching[0]
        job.remote_post_id, job.remote_status = p.id, p.status
        events.transition(db, "publication", job, S.SCHEDULED_REMOTE, "post remoto individuato in riconciliazione (nessun duplicato)")
    elif len(matching) == 0:
        job.next_attempt_at = utcnow()
        events.transition(db, "publication", job, S.RETRY_PENDING, "nessun post remoto trovato: ritento in sicurezza")
    else:
        events.transition(db, "publication", job, S.PUB_NEEDS_REVIEW, "più post remoti compatibili: verifica manuale per evitare duplicati")
        job.error = "Ambiguità: più post remoti compatibili"


def reconcile_job(db: Session, job: PublicationJob) -> None:
    st = ss.get_settings(db)
    if job.status == S.SCHEDULING and not job.remote_post_id:
        recover_scheduling(db, job)
        return
    if not job.remote_post_id:
        return
    try:
        publisher = factory.publisher(db, st)
        p = publisher.get_post(job.remote_post_id)
    except ClipyError as exc:
        job.error = exc.message
        return
    job.last_reconciled_at = utcnow()
    if p is None:
        events.transition(db, "publication", job, S.PUB_NEEDS_REVIEW, "post remoto non trovato (cancellato?)")
        return
    job.remote_status, job.remote_url = p.status, p.url or job.remote_url
    if p.status == "sent":
        events.transition(db, "publication", job, S.PUBLISHED, "confermato dallo stato remoto")
        job.error = None
        events.emit(db, "publisher", "published", f"Pubblicato (confermato dallo stato remoto){' [SIMULATO]' if job.simulated else ''}", job_id=job.id, clip_id=job.clip_id)
    elif p.status == "error":
        job.error = p.error or "Errore remoto"
        events.transition(db, "publication", job, S.PUB_FAILED, job.error)
    elif p.status == "draft":
        events.transition(db, "publication", job, S.PUB_NEEDS_REVIEW, "Buffer richiede un passaggio manuale (notification publishing)")


def cleanup_assets(db: Session, st: ProductSettings) -> None:
    cutoff = utcnow() - timedelta(hours=st.publishing.media_cleanup_hours)
    for j in db.scalars(select(PublicationJob).where(PublicationJob.status == S.PUBLISHED, PublicationJob.asset_key.is_not(None), PublicationJob.last_reconciled_at < cutoff)):
        try:
            factory.storage(db, st, for_live=False).delete(j.asset_key or "")
            j.asset_key = None
        except ClipyError:
            pass


def tick(db: Session, now=None) -> dict:
    """Un ciclo del publisher: ricostruisce la coda, invia i job in scadenza (entro lookahead), riconcilia."""
    now = now or utcnow()
    st = ss.get_settings(db)
    queue.rebuild(db, now)
    db.commit()
    horizon = now + timedelta(hours=st.publishing.lookahead_hours)
    due = list(db.scalars(select(PublicationJob).where(
        PublicationJob.status.in_([S.QUEUED_LOCAL, S.RETRY_PENDING]), PublicationJob.paused.is_(False), PublicationJob.due_at <= horizon)
        .order_by(PublicationJob.due_at.asc())))
    sent = 0
    for j in due:
        if j.next_attempt_at and j.next_attempt_at > now:
            continue
        process_job(db, j.id)
        sent += 1
    reconciled = 0
    for j in db.scalars(select(PublicationJob).where(PublicationJob.status.in_([S.SCHEDULED_REMOTE, S.SCHEDULING]))):
        if j.status == S.SCHEDULING and j.next_attempt_at and j.next_attempt_at > now:
            continue
        reconcile_job(db, j)
        reconciled += 1
    cleanup_assets(db, st)
    db.commit()
    return {"processed": sent, "reconciled": reconciled}


def retry_job(db: Session, job_id: str) -> PublicationJob:
    j = queue.get_job(db, job_id)
    if j.status not in (S.PUB_FAILED, S.PUB_NEEDS_REVIEW, S.DRY_RUN_OK, S.CANCELLED, S.RETRY_PENDING):
        raise ClipyError(f"Retry non consentito per stato {j.status}", code="bad_state", status_code=409)
    if j.remote_post_id:  # esiste già un post remoto: riconcilia, non ricreare
        reconcile_job(db, j)
        return j
    j.asset_url, j.error, j.next_attempt_at = None, None, None
    j.due_at = max(j.due_at, utcnow())
    events.transition(db, "publication", j, S.QUEUED_LOCAL, "retry manuale")
    return j


def reconcile_now(db: Session, job_id: str) -> PublicationJob:
    j = queue.get_job(db, job_id)
    reconcile_job(db, j)
    return j


