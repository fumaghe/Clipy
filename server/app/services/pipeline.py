"""Pipeline clip: download → analisi → render → review gate. Ogni step è idempotente e ripartibile."""
from __future__ import annotations

import hashlib
import random
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.adapters.ai.base import AnalysisContext, Transcript
from app.adapters.video import ffmpeg
from app.adapters.video.renderer import FFmpegRenderer, fingerprint
from app.core.config import get_env
from app.core.errors import ClipyError, Conflict, PermanentError, TemporaryError
from app.core.timeutil import utcnow
from app.db.models import Analysis, Clip, PublicationJob, RenderJob, Review, Template
from app.domain import review_gate
from app.domain import states as S
from app.services import events, factory, leases
from app.services import settings_service as ss
from app.services.templates import TemplateConfig, get_template

MAX_STEP_ATTEMPTS = 4


def backoff(attempts: int) -> timedelta:
    return timedelta(seconds=min(2 ** attempts * 15, 1800) * random.uniform(0.8, 1.2))


# ---------------------------------------------------------------- download
def step_download(db: Session, clip: Clip) -> None:
    st = ss.get_settings(db)
    tw = st.twitch
    now = utcnow()
    source = factory.clip_source(db, st)
    dl = factory.downloader(db, st, source)
    if clip.waiting_since is None:
        clip.waiting_since = now
    try:
        urls = source.get_download_urls(clip.broadcaster_id, [clip.source_clip_id]).get(clip.source_clip_id)
        if urls is None or (not urls.portrait and not urls.landscape):
            raise TemporaryError("Twitch non ha ancora link di download per la clip", code="no_download_url")
        use_portrait = bool(urls.portrait) and tw.prefer_portrait
        clip.portrait_available = bool(urls.portrait)
        if not use_portrait and tw.prefer_portrait and not urls.portrait:
            deadline = clip.waiting_since + timedelta(minutes=tw.portrait_wait_minutes)
            if now < deadline:  # attende brevemente che Twitch generi la versione portrait
                events.transition(db, "clip", clip, S.WAITING_FOR_MEDIA, "portrait non ancora disponibile")
                clip.next_attempt_at = now + timedelta(seconds=min(60, max(5, tw.portrait_wait_minutes * 6)))
                return
        url = urls.portrait if use_portrait else (urls.landscape or urls.portrait)
        clip.landscape_fallback = not use_portrait
        dest = get_env().dir("originals") / f"{clip.id}.mp4"
        dl.download(url, dest, tw.max_download_mb * 1024 * 1024)
        try:
            info = ffmpeg.probe(dest)  # valida integrità: un file troncato/corrotto NON è un video valido
        except PermanentError as exc:
            dest.unlink(missing_ok=True)
            if exc.code == "ffprobe_missing":
                raise
            raise TemporaryError(f"File scaricato incompleto o corrotto, lo riscarico: {exc.message}", code="download_corrupt") from exc
        if info["duration"] < 1:
            dest.unlink(missing_ok=True)
            raise PermanentError("Clip troppo corta o corrotta", code="bad_media")
        clip.original_path = str(dest)
        clip.checksum = hashlib.sha256(dest.read_bytes()).hexdigest()
        clip.media_info = {**(clip.media_info or {}), **info}
        clip.is_portrait_file = ffmpeg.is_portrait(info)
        if clip.landscape_fallback and clip.is_portrait_file:
            clip.landscape_fallback = False
        clip.error = None
        events.transition(db, "clip", clip, S.DOWNLOADED, "portrait" if clip.is_portrait_file else "landscape (fallback)")
        events.emit(db, "downloader", "downloaded", f"Scaricata {clip.source_clip_id} ({'portrait' if clip.is_portrait_file else 'landscape'})", clip_id=clip.id)
    except TemporaryError as exc:
        clip.download_attempts += 1
        clip.error = exc.message
        if clip.download_attempts >= 6:
            events.transition(db, "clip", clip, S.FAILED, exc.message)
        else:
            events.transition(db, "clip", clip, S.WAITING_FOR_MEDIA, exc.message)
            clip.next_attempt_at = utcnow() + backoff(clip.download_attempts)
        events.emit(db, "downloader", "download_retry", exc.message, severity="warning", clip_id=clip.id)
    except ClipyError as exc:
        clip.error = exc.message
        events.transition(db, "clip", clip, S.FAILED, exc.message)
        events.emit(db, "downloader", "download_failed", exc.message, severity="error", clip_id=clip.id)


# ---------------------------------------------------------------- analisi
def _recent_titles(db: Session, clip: Clip, n: int = 30) -> list[str]:
    rows = db.scalars(select(Clip.overlay_title).where(Clip.id != clip.id, Clip.overlay_title.is_not(None)).order_by(Clip.discovered_at_utc.desc()).limit(n))
    return [r for r in rows if r]


def build_context(db: Session, clip: Clip, transcript: Transcript, frames: list[Path], *, variation: int = 0, avoid: list[str] | None = None) -> AnalysisContext:
    ai = ss.get_settings(db).ai
    return AnalysisContext(
        clip_title=clip.title, game_name=clip.game_name, transcript=transcript, frames=frames, duration=clip.duration, language=ai.language,
        tone=ai.tone, prompt_extra=ai.prompt_extra, banned_words=ai.banned_words, max_emoji=ai.max_emoji, max_hashtags=ai.max_hashtags,
        max_overlay_chars=ai.max_overlay_chars, avoid_titles=avoid or [], variation=variation, budget_usd=ai.budget_usd_per_clip,
        demo_fixture=(clip.media_info or {}).get("demo_fixture") if clip.is_demo else None)


def _apply_analysis(db: Session, clip: Clip, transcript: Transcript, visual_notes: list[str], res, *, variation_note: str = "") -> Analysis:
    out = res.output
    flags = list(out.quality_flags)
    if res.simulated and not clip.is_demo:
        flags.append("simulated_analysis")
    a = Analysis(clip_id=clip.id, transcript=transcript.text, segments=transcript.segments, language=transcript.language or out.language,
                 asr_engine=transcript.engine, asr_available=transcript.available, visual_notes=visual_notes,
                 structured=out.model_dump(), confidence=out.confidence, quality_flags=flags, prompt_version=res.prompt_version,
                 provider=res.provider, model=res.model, simulated=res.simulated or transcript.simulated, tokens_in=res.tokens_in,
                 tokens_out=res.tokens_out, cost_usd=res.cost_usd, timings=res.timings)
    db.add(a)
    clip.overlay_title, clip.caption, clip.hashtags = out.overlay_title, out.tiktok_caption, out.hashtags
    clip.content_version += 1
    db.flush()
    return a


def step_analyze(db: Session, clip: Clip) -> None:
    st = ss.get_settings(db)
    video = Path(clip.original_path or "")
    if not video.exists():
        clip.error = "File originale mancante"
        events.transition(db, "clip", clip, S.FAILED, clip.error)
        return
    events.transition(db, "clip", clip, S.ANALYZING)
    db.commit()
    try:
        if st.ai.transcription == "off" and not clip.is_demo:
            tr = Transcript("", [], "", "off", available=False, note="ASR disattivato")
        else:
            tr = factory.transcriber(db, st, clip.is_demo).transcribe(video, st.ai.language)
        frames: list[Path] = []
        if st.ai.vision_enabled and not clip.is_demo:
            frames = ffmpeg.extract_frames(video, get_env().dir("temp") / f"frames-{clip.id}", st.ai.frame_interval_seconds, st.ai.max_frames, clip.duration)
        ctx = build_context(db, clip, tr, frames)
        res = factory.analyzer(db, st, clip.is_demo).analyze(ctx)
        notes: list[str] = []
        if clip.is_demo:
            from app.adapters.video.fixtures import FIXTURES

            fx = FIXTURES.get(ctx.demo_fixture or "")
            notes = [f"[SIMULATO] {v}" for v in (fx.visual if fx else ())]
        elif frames:
            notes = [res.output.scene_summary] if res.output.scene_summary else []
        _apply_analysis(db, clip, tr, notes, res)
        clip.error = None
        events.transition(db, "clip", clip, S.ANALYZED)
        events.emit(db, "ai", "analyzed", f"Analisi completata ({res.provider}{' simulato' if res.simulated else ''}, ~${res.cost_usd})", clip_id=clip.id)
    except TemporaryError as exc:
        clip.step_attempts += 1
        events.emit(db, "ai", "analysis_retry", exc.message, severity="warning", clip_id=clip.id)
        clip.error, clip.next_attempt_at = exc.message, utcnow() + backoff(clip.step_attempts)
        if clip.step_attempts >= MAX_STEP_ATTEMPTS:
            _analysis_fallback(db, clip, exc)  # provider irraggiungibile: la clip NON si perde, va in revisione
        else:
            events.transition(db, "clip", clip, S.DOWNLOADED, exc.message)
    except ClipyError as exc:
        if exc.code in ("ffmpeg_missing", "ffprobe_missing"):
            clip.error = exc.message
            events.transition(db, "clip", clip, S.FAILED, exc.message)
            events.emit(db, "ai", "analysis_failed", exc.message, severity="error", clip_id=clip.id)
        else:
            _analysis_fallback(db, clip, exc)  # chiave rifiutata, budget, risposta non valida...: revisione con motivazione esplicita
    finally:
        for p in (get_env().dir("temp")).glob(f"frames-{clip.id}*"):
            import shutil

            shutil.rmtree(p, ignore_errors=True)


def _analysis_fallback(db: Session, clip: Clip, exc: ClipyError) -> None:
    """Analisi AI non riuscita: crea un'analisi descrittiva marcata `analysis_failed` così il flusso prosegue
    (render → review gate → NEEDS_REVIEW con motivo) senza bloccare la coda né inventare contenuti."""
    from app.adapters.ai.analyzers import fallback_result

    last = latest_analysis(db, clip)
    tr = Transcript(last.transcript, last.segments, last.language, last.asr_engine, last.asr_available, last.simulated) if last else \
        Transcript("", [], "", "none", available=False, note="analisi non completata")
    ctx = build_context(db, clip, tr, [])
    res = fallback_result(ctx, f"{exc.message} [{exc.code}]")
    _apply_analysis(db, clip, tr, [], res)
    clip.error = None
    events.transition(db, "clip", clip, S.ANALYZED, "analisi AI non riuscita: fallback → revisione")
    events.emit(db, "ai", "analysis_fallback", f"Analisi AI non riuscita ({exc.code}): la clip andrà in revisione", severity="warning", clip_id=clip.id)


def regenerate_content(db: Session, clip: Clip) -> None:
    """Rigenera titolo/caption senza ripetere download né ASR (riusa la trascrizione dell'ultima analisi)."""
    last = latest_analysis(db, clip)
    if last is None:
        raise ClipyError("Nessuna analisi precedente: esegui prima l'analisi", code="no_analysis", status_code=409)
    st = ss.get_settings(db)
    tr = Transcript(last.transcript, last.segments, last.language, last.asr_engine, last.asr_available, last.simulated)
    variation = db.query(Analysis).filter(Analysis.clip_id == clip.id).count()
    avoid = [clip.overlay_title or ""] + [a.structured.get("overlay_title", "") for a in clip.analyses][-3:]
    ctx = build_context(db, clip, tr, [], variation=variation, avoid=[x for x in avoid if x])
    res = factory.analyzer(db, st, clip.is_demo).analyze(ctx)
    _apply_analysis(db, clip, tr, last.visual_notes, res)
    invalidate_render(db, clip)
    events.emit(db, "ai", "regenerated", "Titolo/caption rigenerati senza nuovo download", clip_id=clip.id)


def latest_analysis(db: Session, clip: Clip) -> Analysis | None:
    return db.scalar(select(Analysis).where(Analysis.clip_id == clip.id).order_by(Analysis.created_at.desc(), Analysis.id.desc()))


# ---------------------------------------------------------------- render
def active_template(db: Session, clip: Clip) -> tuple[Template, TemplateConfig]:
    st = ss.get_settings(db)
    t = get_template(db, clip.template_id or st.render.active_template_id)
    return t, TemplateConfig.model_validate(t.config)


def invalidate_render(db: Session, clip: Clip, *, to_status: str = S.ANALYZED) -> None:
    """Invalida render e job locali dipendenti (senza toccare il sorgente)."""
    clip.render_path, clip.render_fingerprint = None, None
    for j in db.scalars(select(PublicationJob).where(PublicationJob.clip_id == clip.id, PublicationJob.status.in_(S.PUB_LOCAL_CANCELLABLE))):
        events.transition(db, "publication", j, S.CANCELLED, "contenuto modificato")
    if clip.status not in (S.REJECTED, S.SKIPPED, S.FAILED) or to_status == S.ANALYZED:
        events.transition(db, "clip", clip, to_status, "render invalidato")
    db.flush()


# ---------------------------------------------------------------- reprocessing esplicito (rianalisi / re-render)
def busy_reason(clip: Clip) -> str | None:
    """Motivo per cui la clip NON accetta comandi che la modificano (None = libera). Fonte unica per API e DTO."""
    if clip.status == S.ANALYZING:
        return "Analisi in corso"
    if clip.status == S.RENDERING:
        return "Render in corso"
    if clip.locked_until is not None and clip.locked_until > utcnow():
        return "Elaborazione in corso"
    return None


def busy_reason_status(clip: Clip) -> str | None:
    """Come `busy_reason` ma ignorando il lock di riga (che `exclusive` ha appena preso noi stessi)."""
    return {S.ANALYZING: "Analisi in corso", S.RENDERING: "Render in corso"}.get(clip.status)


@contextmanager
def exclusive(db: Session, clip: Clip) -> Iterator[Clip]:
    """Sezione critica su una clip: lock di riga (stesso del worker) + commit atomico delle modifiche.

    Il worker non può avanzare la clip mentre l'API la modifica e viceversa. Se la clip è già in elaborazione →
    409 `busy` (previsto, non è un guasto). In caso di errore la transazione viene annullata per intero."""
    cid = clip.id
    if not leases.lock_row(db, Clip, cid, 300):
        raise Conflict("La clip è già in elaborazione: attendi che finisca e riprova", code="busy")
    try:
        db.refresh(clip)
        reason = busy_reason_status(clip)
        if reason:
            raise Conflict(f"{reason}: attendi che finisca e riprova", code="busy")
        yield clip
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        leases.unlock_row(db, Clip, cid)


def assert_reprocessable(db: Session, clip: Clip) -> None:
    """Un contenuto già inviato (o possibilmente inviato) al provider non si rianalizza né si ri-renderizza:
    il nuovo content_version genererebbe un secondo post della stessa clip."""
    live = db.scalar(select(PublicationJob).where(
        PublicationJob.clip_id == clip.id, PublicationJob.status != S.CANCELLED,
        or_(PublicationJob.status.in_(S.PUB_REMOTE_LIVE), PublicationJob.remote_post_id.is_not(None), PublicationJob.locked_until > utcnow())))
    if live is not None:
        raise Conflict("La clip è già in pubblicazione, pianificata o pubblicata: non si può rielaborare senza creare un duplicato", code="publication_locked")


def snapshot_content(db: Session, clip: Clip, note: str) -> None:
    """Conserva titolo/caption/hashtag correnti (anche se modificati a mano) prima che una rielaborazione li sostituisca."""
    if not (clip.overlay_title or clip.caption):
        return
    db.add(Review(clip_id=clip.id, policy_version="reprocess", review_state=clip.status, actor="system", note=note, reviewed_at=utcnow(),
                  changes={"previous": {"overlay_title": clip.overlay_title, "caption": clip.caption, "hashtags": list(clip.hashtags or []),
                                        "content_version": clip.content_version, "human_edited": clip.human_edited}}))


def reprocess(db: Session, clip: Clip, *, stage: str, template_id: str | None = None) -> Clip:
    """Rianalisi (`stage="analyze"`: → DOWNLOADED) o nuovo render (`stage="render"`: → ANALYZED) di una clip già elaborata.

    Atomico (lock di riga + una sola transazione), idempotente (una richiesta ripetuta mentre la clip è già in coda non fa nulla),
    senza duplicati (vedi `assert_reprocessable`) e senza perdite: analisi, review, file e testi precedenti restano.
    I job locali/simulati vengono annullati; quando la clip torna idonea la coda ne crea uno nuovo per la nuova versione."""
    if stage not in ("analyze", "render"):
        raise ValueError(stage)
    target = S.DOWNLOADED if stage == "analyze" else S.ANALYZED
    with exclusive(db, clip) as c:
        if c.status not in S.CLIP_REPROCESSABLE:
            if stage == "analyze" and c.status in (S.DISCOVERED, S.WAITING_FOR_MEDIA):
                raise Conflict("Originale non scaricato: usa Retry", code="no_original")
            return c  # già nella coda di elaborazione automatica: richiesta ripetuta = no-op
        if stage == "analyze" and not (c.original_path and Path(c.original_path).exists()):
            raise Conflict("Originale non scaricato: usa Retry", code="no_original")
        if stage == "render" and not latest_analysis(db, c):
            raise Conflict("Esegui prima l'analisi", code="no_analysis")
        assert_reprocessable(db, c)
        if template_id is not None:
            c.template_id = template_id
        if stage == "analyze":
            snapshot_content(db, c, "contenuto precedente conservato prima della rianalisi")
            c.approve_after_render = False
        elif c.status == S.APPROVED:
            c.approve_after_render, c.human_edited = True, True  # il nuovo render riconferma l'approvazione umana
        c.render_path, c.render_fingerprint = None, None
        c.content_version += 1  # nuovo file di render (mai sovrascritto in uso) e nuovo job per la nuova versione
        for j in db.scalars(select(PublicationJob).where(PublicationJob.clip_id == c.id, PublicationJob.status.in_(S.PUB_LOCAL_CANCELLABLE))):
            events.transition(db, "publication", j, S.CANCELLED, f"reprocessing ({stage})")
        c.error, c.step_attempts, c.next_attempt_at = None, 0, None
        events.transition(db, "clip", c, target, f"reprocess:{stage}")
        events.emit(db, "pipeline", "reprocess", f"Reprocessing richiesto ({stage}): {target}", clip_id=c.id)
    return clip


def step_render(db: Session, clip: Clip) -> None:
    st = ss.get_settings(db)
    tpl, cfg = active_template(db, clip)
    text = clip.overlay_title or ""
    fp = fingerprint(clip.checksum or "", text, cfg, {"fps": st.render.fps, "v": tpl.version})
    out_dir = get_env().dir("rendered")
    out = out_dir / f"{clip.id}-v{clip.content_version}.mp4"
    events.transition(db, "clip", clip, S.RENDERING)
    db.commit()
    warnings: list[str] = []
    checks: dict = {}
    if clip.render_fingerprint == fp and clip.render_path and Path(clip.render_path).exists():
        last = db.scalar(select(RenderJob).where(RenderJob.clip_id == clip.id, RenderJob.status == "DONE").order_by(RenderJob.finished_at.desc()))
        checks, warnings = (last.checks, last.warnings) if last else ({}, [])
        job = None
    else:
        job = RenderJob(clip_id=clip.id, template_id=tpl.id, template_version=tpl.version, reference_verified=tpl.reference_verified, fingerprint=fp)
        db.add(job)
        db.commit()
        try:
            r = FFmpegRenderer(st.render.fps, st.render.video_bitrate_k, st.render.audio_bitrate_k, st.render.preset).render(Path(clip.original_path or ""), text, cfg, out, source_checksum=clip.checksum or "")
            checks, warnings = r.checks, r.warnings
            job.status, job.output_path, job.checks, job.warnings, job.finished_at = "DONE", str(out), checks, warnings, utcnow()
            clip.render_path, clip.render_fingerprint = str(out), fp
        except TemporaryError as exc:  # es. timeout di ffmpeg: il processo è già stato terminato, si riprova più tardi
            job.status, job.error, job.finished_at = "FAILED", exc.message, utcnow()
            clip.step_attempts += 1
            clip.error, clip.next_attempt_at = exc.message, utcnow() + backoff(clip.step_attempts)
            events.transition(db, "clip", clip, S.FAILED if clip.step_attempts >= MAX_STEP_ATTEMPTS else S.ANALYZED, exc.message)
            events.emit(db, "renderer", "render_retry", exc.message, severity="warning", clip_id=clip.id)
            return
        except ClipyError as exc:
            job.status, job.error, job.finished_at = "FAILED", exc.message, utcnow()
            clip.error = exc.message
            events.transition(db, "clip", clip, S.FAILED, exc.message)
            events.emit(db, "renderer", "render_failed", exc.message, severity="error", clip_id=clip.id)
            return
    clip.template_id = tpl.id if clip.template_id else clip.template_id
    clip.error = None
    events.transition(db, "clip", clip, S.READY)
    events.emit(db, "renderer", "rendered", f"Render pronto ({tpl.name}{'' if tpl.reference_verified else ', reference NON verificata'})", clip_id=clip.id)
    run_gate(db, clip, checks, warnings)


# ---------------------------------------------------------------- review gate
def run_gate(db: Session, clip: Clip, checks: dict, warnings: list[str]) -> None:
    st = ss.get_settings(db)
    a = latest_analysis(db, clip)
    s = a.structured if a else {}
    inp = review_gate.GateInput(
        transcript=a.transcript if a else "", visual_notes=a.visual_notes if a else [], game_name=clip.game_name, clip_title=clip.title,
        overlay_title=clip.overlay_title or "", caption=clip.caption or "", hashtags=clip.hashtags or [], confidence=a.confidence if a else 0.0,
        llm_requires_review=bool(s.get("requires_review")), llm_reason=s.get("reason"), quality_flags=(a.quality_flags if a else []),
        asr_available=a.asr_available if a else True, render_checks=checks, render_warnings=warnings, landscape_fallback=clip.landscape_fallback,
        max_caption_length=st.publishing.max_caption_length, banned_words=st.ai.banned_words, max_overlay_chars=st.ai.max_overlay_chars,
        max_emoji=st.ai.max_emoji, recent_titles=_recent_titles(db, clip), simulated_scenario=clip.demo_scenario if clip.is_demo else "",
        asr_disabled=bool(a and a.asr_engine == "off"), sensitive_terms=st.review.sensitive_terms)
    if inp.landscape_fallback and st.twitch.landscape_fallback == "review":
        inp.render_warnings = [*inp.render_warnings, "crop_unvalidated"]
    res = review_gate.evaluate(inp, st.review)
    if "simulated_analysis" in (a.quality_flags if a else []):
        res.flags.append("simulated_analysis")
        res.reasons.append({"code": "simulated_analysis", "message": "Analisi AI SIMULATA su clip reale (nessun provider configurato): serve revisione."})
        res.state = "NEEDS_REVIEW"
    if clip.approve_after_render and clip.human_edited:
        new_state, actor = S.APPROVED, "human"
        db.add(Review(clip_id=clip.id, policy_version=res.policy_version, review_state=S.APPROVED, reasons=res.reasons, flags=res.flags,
                      human_override=True, actor=actor, reviewed_at=utcnow(), note="Approvata dopo modifica umana"))
        clip.approve_after_render = False
    else:
        new_state = S.NEEDS_REVIEW if res.needs_review else S.AUTO_READY
        db.add(Review(clip_id=clip.id, policy_version=res.policy_version, review_state=new_state, reasons=res.reasons, flags=res.flags, actor="system"))
    events.transition(db, "clip", clip, new_state, ", ".join(res.flags) or "review gate ok")
    events.emit(db, "review", "gate", f"Review gate → {new_state}" + (f" ({', '.join(res.flags)})" if res.flags else ""), clip_id=clip.id)


# ---------------------------------------------------------------- orchestrazione
def next_clip_id(db: Session) -> tuple[str, str] | None:
    now = utcnow()
    order = [S.RENDERING, S.ANALYZING, S.ANALYZED, S.DOWNLOADED, S.DISCOVERED, S.WAITING_FOR_MEDIA]
    for status in order:
        q = select(Clip.id).where(Clip.status == status, Clip.paused.is_(False), or_(Clip.locked_until.is_(None), Clip.locked_until < now))
        if status in (S.DISCOVERED, S.WAITING_FOR_MEDIA, S.DOWNLOADED, S.ANALYZED):
            q = q.where(or_(Clip.next_attempt_at.is_(None), Clip.next_attempt_at <= now))
        row = db.scalar(q.order_by(Clip.created_at_utc.asc()).limit(1))
        if row:
            return row, status
    return None


def advance_one(db: Session) -> bool:
    nxt = next_clip_id(db)
    if not nxt:
        return False
    cid, status = nxt
    if not leases.lock_row(db, Clip, cid, 900):
        return True
    try:
        clip = db.get(Clip, cid)
        assert clip is not None
        db.refresh(clip)
        if clip.status != status or clip.paused:  # l'API ha cambiato stato tra la selezione e il lock: nulla da fare, si riseleziona
            return True
        if status in (S.DISCOVERED, S.WAITING_FOR_MEDIA):
            step_download(db, clip)
        elif status in (S.DOWNLOADED, S.ANALYZING):
            step_analyze(db, clip)
        elif status in (S.ANALYZED, S.RENDERING):
            step_render(db, clip)
        db.commit()
    except Exception as exc:  # errore inatteso: non blocca la pipeline
        db.rollback()
        clip = db.get(Clip, cid)
        if clip:
            clip.error = f"Errore inatteso: {exc}"[:500]
            clip.step_attempts += 1
            if clip.step_attempts >= MAX_STEP_ATTEMPTS:
                events.transition(db, "clip", clip, S.FAILED, clip.error)
            else:
                clip.next_attempt_at = utcnow() + backoff(clip.step_attempts)
            events.emit(db, "pipeline", "unexpected_error", clip.error, severity="error", clip_id=cid)
            db.commit()
    finally:
        leases.unlock_row(db, Clip, cid)
    return True


def run_until_idle(db: Session, max_steps: int = 200) -> int:
    n = 0
    while n < max_steps and advance_one(db):
        n += 1
    return n


def retry_clip(db: Session, clip: Clip) -> None:
    """Riporta una clip FAILED al primo step ripartibile (non scarta file)."""
    if clip.original_path and Path(clip.original_path).exists():
        target = S.ANALYZED if latest_analysis(db, clip) else S.DOWNLOADED
    else:
        target = S.DISCOVERED
    clip.step_attempts, clip.download_attempts, clip.next_attempt_at, clip.error, clip.locked_until = 0, 0, None, None, None
    clip.waiting_since = None
    events.transition(db, "clip", clip, target, "retry manuale")
