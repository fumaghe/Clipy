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
from app.adapters.publisher.buffer import same_text
from app.core.errors import ClipyError, IncompleteResult, TemporaryError
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
        job.next_attempt_at = utcnow() + max(backoff(job.attempts), timedelta(seconds=exc.retry_after or 0))
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


def job_text(job: PublicationJob, st: ProductSettings) -> str:
    """Testo ESATTO inviato a Buffer: usato sia dalla create sia dalla riconciliazione (stessa funzione)."""
    clip = job.clip
    return captions.compose_post_text(job.caption or clip.caption or "", job.hashtags or clip.hashtags or [], st.publishing.max_caption_length)


AMBIGUOUS_CODES = ("buffer_timeout", "buffer_unreachable_after_send", "buffer_network", "buffer_5xx", "buffer_bad_response")
MIN_EMPTY_RECONCILES = 2  # ricerche complete e vuote richieste prima di ritentare una create dall'esito incerto
RECONCILE_GRACE = timedelta(minutes=5)


def _is_ambiguous(exc: ClipyError) -> bool:
    return bool(getattr(exc, "ambiguous", False)) or (isinstance(exc, TemporaryError) and exc.code in AMBIGUOUS_CODES and exc.code != "buffer_unreachable")


def _process(db: Session, job: PublicationJob) -> None:
    st = ss.get_settings(db)
    # il job potrebbe essere stato elaborato da un altro worker dopo la selezione: rilegge lo stato sotto lock
    if job.status not in (S.QUEUED_LOCAL, S.RETRY_PENDING) or job.paused:
        return
    clip = job.clip
    if clip.status not in S.CLIP_ELIGIBLE or not clip.render_path or not Path(clip.render_path).exists():
        events.transition(db, "publication", job, S.CANCELLED, "clip non più idonea o render mancante")
        return
    text = job_text(job, st)
    mode = effective_mode(st)
    job.fingerprint = _fingerprint(job, text)
    if mode == "dry_run":
        # zero richieste esterne: né upload, né create
        job.simulated, job.error = True, None
        job.remote_status = "dry_run"
        events.transition(db, "publication", job, S.DRY_RUN_OK, "DRY RUN: nessuna richiesta esterna")
        events.emit(db, "publisher", "dry_run", f"[DRY RUN] Simulata pubblicazione di {clip.source_clip_id} per {job.due_at.isoformat()}Z", job_id=job.id, clip_id=clip.id)
        return
    if mode == "live" and job.due_at <= utcnow() + timedelta(seconds=60) and not job.remote_post_id:
        # lo slot è scaduto (retry tardivo/riavvio): una createPost con dueAt nel passato potrebbe pubblicare subito
        events.transition(db, "publication", job, S.QUEUED_LOCAL, "slot scaduto prima dell'invio: verrà riassegnato")
        job.next_attempt_at = None
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
        job.send_started_at, job.remote_state, job.reconcile_empty_count = utcnow(), S.REMOTE_SENDING, 0
        db.commit()
        if mode == "live":
            remote_count = publisher.count_scheduled(job.target_channel_id)
            if remote_count >= st.publishing.remote_max_scheduled:
                job.remote_state, job.send_started_at = S.REMOTE_LOCAL, None
                events.transition(db, "publication", job, S.QUEUED_LOCAL, "coda remota piena: resta in coda locale")
                return
        remote = publisher.create_post(PublishRequest(job.target_channel_id, text, job.asset_url or "", job.due_at, job.fingerprint))
        job.remote_post_id, job.remote_status, job.remote_url = remote.id, remote.status, remote.url
        job.remote_state = remote_state_for(remote.status)
        job.simulated = bool(getattr(publisher, "simulated", False))
        job.error = None
        events.transition(db, "publication", job, S.SCHEDULED_REMOTE, "pianificato (NON ancora pubblicato)")
        events.emit(db, "publisher", "scheduled", f"Pianificato su {publisher.name} ({remote.id}){' [SIMULATO]' if job.simulated else ''}", job_id=job.id, clip_id=clip.id)
    except ClipyError as exc:
        if job.status == S.SCHEDULING and _is_ambiguous(exc):
            # esito sconosciuto: NON ritentare la create alla cieca → riconciliazione con ricerca remota completa
            job.error = exc.message
            job.next_attempt_at = utcnow() + timedelta(seconds=max(120, exc.retry_after or 0))
            events.emit(db, "publisher", "create_ambiguous", "Esito della create sconosciuto: verrà riconciliato prima di ogni retry", severity="warning", job_id=job.id)
        else:
            if job.status == S.SCHEDULING and not job.remote_post_id:
                job.remote_state, job.send_started_at = S.REMOTE_LOCAL, None  # rifiutata prima dell'esecuzione: nulla di remoto
            _fail(db, job, exc, st)


def remote_state_for(status: str) -> str:
    return {"scheduled": S.REMOTE_SCHEDULED, "sending": S.REMOTE_SENDING, "sent": S.REMOTE_BUFFER_SENT, "error": S.REMOTE_BUFFER_ERROR}.get(status, S.REMOTE_UNCERTAIN)


def recover_scheduling(db: Session, job: PublicationJob) -> None:
    """Job rimasto in SCHEDULING senza ID remoto (crash/timeout): cerca il post remoto prima di ritentare.

    Un secondo createPost è ammesso solo dopo >= MIN_EMPTY_RECONCILES ricerche COMPLETE e vuote a distanza di tempo;
    se la ricerca è incompleta o i riscontri sono ambigui si passa a NEEDS_REVIEW (mai un post duplicato)."""
    st = ss.get_settings(db)
    now = utcnow()
    try:
        publisher = factory.publisher(db, st)
        found = publisher.find_posts(job.target_channel_id, job.due_at)
    except IncompleteResult as exc:
        job.error = exc.message
        events.transition(db, "publication", job, S.PUB_NEEDS_REVIEW, "ricerca remota incompleta: non posso escludere un post già creato")
        job.remote_state = S.REMOTE_UNCERTAIN
        return
    except ClipyError as exc:
        job.error = exc.message
        job.next_attempt_at = now + max(backoff(job.attempts + 1), timedelta(seconds=exc.retry_after or 0))
        return
    text = job_text(job, st)
    same = [p for p in found if same_text(p.text, text)]
    if len(same) == 1:
        p = same[0]
        job.remote_post_id, job.remote_status, job.remote_state, job.remote_url = p.id, p.status, remote_state_for(p.status), p.url
        job.error = None
        events.transition(db, "publication", job, S.SCHEDULED_REMOTE, "post remoto individuato in riconciliazione (nessun duplicato)")
    elif len(same) > 1 or found:
        # più riscontri, oppure post allo stesso orario con testo diverso: non si può stabilire con certezza
        job.error = "Ambiguità: post remoti compatibili ma non identificabili con certezza" if found else job.error
        job.remote_state = S.REMOTE_UNCERTAIN
        events.transition(db, "publication", job, S.PUB_NEEDS_REVIEW, "riscontri remoti ambigui: verifica manuale per evitare duplicati")
    else:
        job.reconcile_empty_count += 1
        started = job.send_started_at or job.created_at
        if job.reconcile_empty_count >= MIN_EMPTY_RECONCILES and now - started >= RECONCILE_GRACE:
            job.next_attempt_at, job.remote_state = now, S.REMOTE_LOCAL
            events.transition(db, "publication", job, S.RETRY_PENDING, "nessun post remoto in ricerche complete ripetute: ritento in sicurezza")
        else:
            job.next_attempt_at = now + timedelta(seconds=60)  # resta SCHEDULING: nessuna nuova create


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
    if job.verification != S.VERIFY_EXTERNAL:
        job.remote_state = remote_state_for(p.status)
    if p.status == "sent":
        events.transition(db, "publication", job, S.PUBLISHED, "Buffer segnala inviato (non verificato su TikTok)")
        job.error = None
        events.emit(db, "publisher", "published", f"Buffer segnala il post come inviato (visibilità su TikTok NON verificata){' [SIMULATO]' if job.simulated else ''}", job_id=job.id, clip_id=job.clip_id)
    elif p.status == "error":
        job.error = p.error or "Errore remoto"
        events.transition(db, "publication", job, S.PUB_FAILED, job.error)
    elif p.status == "draft":
        events.transition(db, "publication", job, S.PUB_NEEDS_REVIEW, "Buffer richiede un passaggio manuale (notification publishing)")
    elif p.status == "unknown":
        if job.due_at + timedelta(hours=6) < utcnow():
            events.transition(db, "publication", job, S.PUB_NEEDS_REVIEW, "stato esterno incerto da oltre 6 ore: verifica su Buffer/TikTok")
    elif p.status == "scheduled" and job.due_at + timedelta(hours=2) < utcnow():
        job.remote_state = S.REMOTE_UNCERTAIN  # ancora 'scheduled' ben oltre lo slot: segnalato in UI, nessuna azione automatica


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
    for j in db.scalars(select(PublicationJob).where(PublicationJob.status == S.UPLOADING_ASSET, (PublicationJob.locked_until.is_(None)) | (PublicationJob.locked_until < now))):
        # crash durante l'upload: nulla è stato inviato a Buffer, si ricomincia (il file orfano viene ripulito dalla retention)
        events.transition(db, "publication", j, S.QUEUED_LOCAL, "upload interrotto: ripreso")
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




def mark_externally_verified(db: Session, job_id: str, note: str = "") -> PublicationJob:
    """Conferma manuale dell'utente: il post è visibile su TikTok. Unico modo di ottenere EXTERNALLY_VERIFIED."""
    j = queue.get_job(db, job_id)
    if j.status != S.PUBLISHED or j.simulated:
        raise ClipyError("Verificabile solo un job per cui Buffer segnala 'inviato' (non simulato)", code="bad_state", status_code=409)
    j.verification, j.remote_state, j.verified_at = S.VERIFY_EXTERNAL, S.REMOTE_VERIFIED, utcnow()
    events.emit(db, "publisher", "externally_verified", f"Pubblicazione verificata dall'utente su TikTok. {note}"[:300], job_id=j.id, clip_id=j.clip_id)
    return j
