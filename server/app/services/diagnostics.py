"""Diagnostica delle integrazioni (Settings). Stati distinti, test SENZA effetti collaterali sulla pubblicazione.

Stati: NOT_CONFIGURED · CONFIGURED · CONNECTED · TEST_FAILED · NEEDS_LIVE_VERIFICATION.
Nessun test pubblica un post, tocca la coda o modifica video esistenti. I test che scrivono (storage) o costano (LLM)
richiedono `confirm=True` esplicito. L'esito del test è valido solo per la configurazione con cui è stato eseguito
(impronta del config + dei segreti): se cambia, lo stato torna a CONFIGURED. Nessun segreto compare nei risultati."""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from pathlib import Path

from sqlalchemy.orm import Session

from app.adapters.ai.transcribe import asr_health
from app.adapters.video import ffmpeg
from app.core.config import get_env
from app.core.errors import ClipyError
from app.core.timeutil import iso, utcnow
from app.db.models import Setting
from app.services import factory
from app.services import settings_service as ss

NOT_CONFIGURED, CONFIGURED, CONNECTED, TEST_FAILED, NEEDS_LIVE = "NOT_CONFIGURED", "CONFIGURED", "CONNECTED", "TEST_FAILED", "NEEDS_LIVE_VERIFICATION"
SERVICES = ("twitch", "buffer", "storage", "llm", "whisper", "ffmpeg")
LABELS = {"twitch": "Twitch", "buffer": "Buffer / TikTok", "storage": "Storage R2/S3", "llm": "Provider LLM", "whisper": "Whisper (ASR locale)", "ffmpeg": "FFmpeg"}
NEEDS_CONFIRM = {"storage": "Carica un file di prova su R2/S3, lo scarica senza credenziali e lo cancella.",
                 "llm": "Invia una richiesta minima al provider (costo trascurabile, ma non nullo).",
                 "whisper": "Carica il modello Whisper e trascrive 1 secondo di silenzio (può scaricare il modello la prima volta)."}


def _fingerprint(db: Session, service: str) -> str:
    st = ss.get_settings(db)
    parts: dict = {"twitch": [st.twitch.login, st.system.demo_mode, ss.get_secret(db, "twitch_client_id"), ss.get_secret(db, "twitch_client_secret"),
                              ss.get_secret(db, "twitch_access_token")],
                   "buffer": [st.publishing.provider, st.publishing.channel_id, st.publishing.organization_id, ss.get_secret(db, "buffer_api_key")],
                   "storage": [st.storage.model_dump(), ss.get_secret(db, "s3_access_key_id"), ss.get_secret(db, "s3_secret_access_key")],
                   "llm": [st.ai.provider, st.ai.model, st.ai.base_url, ss.get_secret(db, "llm_api_key")],
                   "whisper": [st.ai.transcription, st.ai.whisper_model, st.ai.whisper_device],
                   "ffmpeg": [ffmpeg.have_binary("ffmpeg"), ffmpeg.have_binary("ffprobe")]}[service]
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:24]


def _load(db: Session) -> dict:
    row = db.get(Setting, "diagnostics")
    return dict(row.value) if row else {}


def _store(db: Session, service: str, entry: dict) -> None:
    data = _load(db)
    data[service] = entry
    row = db.get(Setting, "diagnostics")
    if row is None:
        db.add(Setting(key="diagnostics", value=data, version=1))
    else:
        row.value = data
    db.flush()


def _configured(db: Session, service: str) -> tuple[str, bool, str, list[str]]:
    """(stato base, simulato?, riepilogo, suggerimenti) senza alcuna chiamata di rete."""
    st = ss.get_settings(db)
    sec = ss.secrets_status(db)
    if service == "twitch":
        if st.system.demo_mode or not sec["twitch_client_id"]:
            return NOT_CONFIGURED, True, "Modalità DEMO: le clip sono simulate (MockTwitch).", ["Inserisci Client ID/Secret e collega l'account Twitch."]
        hints = []
        if not sec["twitch_client_secret"]:
            hints.append("Manca il Client Secret.")
        if not sec["twitch_access_token"]:
            hints.append("Account non ancora autorizzato (OAuth).")
        return (CONFIGURED if not hints else NOT_CONFIGURED), False, f"Canale {st.twitch.login}", hints
    if service == "buffer":
        if st.publishing.provider == "mock":
            return NOT_CONFIGURED, True, "Publisher DEMO (simulato): nessun post reale possibile.", ["Seleziona 'Usa Buffer reale' e inserisci la API key."]
        if not sec["buffer_api_key"]:
            return NOT_CONFIGURED, False, "API key Buffer assente.", ["Genera una API key nelle impostazioni sviluppatore Buffer."]
        hints = [] if st.publishing.channel_id else ["Testa la connessione e seleziona il canale TikTok."]
        return CONFIGURED, False, "API key presente" + (f", canale {st.publishing.channel_id}" if st.publishing.channel_id else ""), hints
    if service == "storage":
        s = st.storage
        if s.provider != "s3":
            return NOT_CONFIGURED, True, "Storage locale (mock): non pubblico, valido solo per demo/dry-run.", ["Configura R2/S3 con URL pubblico HTTPS."]
        miss = [n for n, v in (("bucket", s.bucket), ("endpoint", s.endpoint_url), ("URL pubblico", s.public_base_url), ("access key", sec["s3_access_key_id"]),
                               ("secret key", sec["s3_secret_access_key"])) if not v]
        if miss:
            return NOT_CONFIGURED, False, "Mancano: " + ", ".join(miss), []
        hints = [] if s.public_base_url.startswith("https://") else ["L'URL pubblico deve essere HTTPS."]
        return CONFIGURED, False, f"Bucket {s.bucket}", hints
    if service == "llm":
        a = st.ai
        if a.provider == "mock":
            return NOT_CONFIGURED, True, "Analisi SIMULATA: le clip reali finiscono sempre in revisione.", ["Scegli un provider (anthropic/openai), modello e API key."]
        miss = [n for n, v in (("modello", a.model), ("API key", sec["llm_api_key"])) if not v]
        return (NOT_CONFIGURED if miss else CONFIGURED), False, (f"Provider {a.provider}, modello {a.model or '?'}" + (f" — mancano: {', '.join(miss)}" if miss else "")), []
    if service == "whisper":
        if st.ai.transcription == "off":
            return NOT_CONFIGURED, False, "Trascrizione disattivata nelle impostazioni.", []
        h = asr_health(st.ai.whisper_model, st.ai.whisper_device)
        if not h["faster_whisper_installed"]:
            return NOT_CONFIGURED, False, "faster-whisper non installato: le clip reali finiranno in revisione (asr_unavailable).", [r"Installa: .\.venv\Scripts\pip install faster-whisper"]
        return CONFIGURED, False, f"Modello {st.ai.whisper_model} ({'in cache' if h['model_cached'] else 'da scaricare al primo uso'})", h["warnings"]
    ok = bool(ffmpeg.have_binary("ffmpeg") and ffmpeg.have_binary("ffprobe"))
    return (CONFIGURED if ok else NOT_CONFIGURED), False, "ffmpeg e ffprobe trovati" if ok else "ffmpeg/ffprobe non trovati", [] if ok else ["winget install Gyan.FFmpeg"]


def entry(db: Session, service: str) -> dict:
    state, simulated, summary, hints = _configured(db, service)
    last = _load(db).get(service)
    out = {"id": service, "label": LABELS[service], "state": state, "simulated": simulated, "summary": summary, "hints": hints,
           "test_requires_confirm": service in NEEDS_CONFIRM, "confirm_text": NEEDS_CONFIRM.get(service), "last_test": None, "details": {}}
    if last and last.get("fingerprint") == _fingerprint(db, service):
        out["state"] = last["state"]
        out["last_test"] = {"at": last["at"], "ok": last["ok"], "message": last["message"], "duration_ms": last.get("duration_ms")}
        out["details"] = last.get("details", {})
        out["simulated"] = bool(last.get("simulated", simulated))
    elif last:
        out["last_test"] = {"at": last["at"], "ok": None, "message": "Configurazione cambiata dopo l'ultimo test: ripeti il test.", "duration_ms": None}
    if state == NOT_CONFIGURED:
        out["state"] = NOT_CONFIGURED  # un risultato precedente non rende valida una configurazione ora incompleta
        out["last_test"] = None if not last else out["last_test"]
    return out


def all_entries(db: Session) -> list[dict]:
    return [entry(db, s) for s in SERVICES]


# ---------------------------------------------------------------- test (nessuna pubblicazione, nessuna modifica alla coda)
def _t_twitch(db: Session) -> tuple[str, str, dict]:
    st = ss.get_settings(db)
    src = factory.clip_source(db, st)
    u = src.resolve_user(st.twitch.login)
    if getattr(src, "simulated", False):
        return NOT_CONFIGURED, "Sorgente SIMULATA (demo): nessun contatto con Twitch.", {"broadcaster_id": u.id, "login": u.login}
    ident = src.tokens.identity()  # type: ignore[attr-defined]  # valida il token (rinnovandolo se scaduto) e legge scope/utente
    from app.adapters.twitch.helix import REQUIRED_SCOPE

    d = {"broadcaster_id": u.id, "broadcaster_login": u.login, "authorized_user": ident["login"], "authorized_user_id": ident["user_id"], "scopes": ident["scopes"]}
    if REQUIRED_SCOPE not in ident["scopes"]:
        return TEST_FAILED, f"Al token manca lo scope {REQUIRED_SCOPE}: ricollega l'account.", d
    if ident["user_id"] != u.id:
        return NEEDS_LIVE, (f"Autorizzato come {ident['login']}, ma il canale è {u.login}: i download funzionano solo se {ident['login']} è editor del canale. "
                            "Verificabile solo scaricando una clip reale."), d
    return CONNECTED, f"Token valido per {ident['login']} (scope OK). L'endpoint di download clip si verifica solo con una clip reale.", d


def _t_buffer(db: Session) -> tuple[str, str, dict]:
    st = ss.get_settings(db)
    pub = factory.publisher(db, st)
    res = pub.test_connection()
    if res.get("simulated"):
        return NOT_CONFIGURED, "Publisher SIMULATO: nessun contatto con Buffer.", {"channels": len(res.get("channels", []))}
    chans = res.get("channels", [])
    d = {"organizations": len(res.get("organizations", [])), "channels": len(chans), "tiktok_channels": res.get("tiktok_channels", 0),
         "eligible_channels": sum(1 for c in chans if c.get("eligible")), "warnings": res.get("warnings", [])}
    sel = next((c for c in chans if c["id"] == st.publishing.channel_id), None)
    if st.publishing.channel_id and sel is None:
        return TEST_FAILED, "Il canale selezionato non esiste più nell'account Buffer.", d
    if sel is not None and not sel.get("eligible"):
        return TEST_FAILED, sel.get("reason") or "Canale non idoneo.", d
    schema = pub.check_schema()  # type: ignore[attr-defined]
    d["schema_check"] = {k: schema[k] for k in ("ok", "checked", "problems") if k in schema}
    if schema.get("checked") and not schema["ok"]:
        return TEST_FAILED, "Schema Buffer diverso dall'adapter: " + "; ".join(schema["problems"][:3]), d
    if not schema.get("checked"):
        return NEEDS_LIVE, "Lettura riuscita ma lo schema non è verificabile (introspezione disattivata): serve un post di prova supervisionato.", d
    return CONNECTED, "Lettura riuscita e schema compatibile. La pubblicazione si verifica solo con un post di prova supervisionato.", d


def _t_storage(db: Session) -> tuple[str, str, dict]:
    st = ss.get_settings(db)
    if st.storage.provider != "s3":
        return NOT_CONFIGURED, "Storage locale (mock): non verificabile come pubblico.", {}
    storage = factory.storage(db, st, for_live=True)
    tmp = get_env().dir("temp") / f"storage-test-{uuid.uuid4().hex[:6]}.mp4"
    ffmpeg.make_fixture(tmp, width=360, height=640, seconds=1, audio=False, label="clipy-storage-test")
    steps: dict = {}
    key = f"clipy-test-{uuid.uuid4().hex}.mp4"
    asset = None
    try:
        asset = storage.upload(Path(tmp), key)
        steps["upload"] = True
        ok, why = storage.verify_public(asset.url)
        steps["anonymous_https"] = ok
        steps["anonymous_https_detail"] = why
        if ok and not asset.url.startswith("https://"):
            steps["anonymous_https"], steps["anonymous_https_detail"] = False, "URL non HTTPS"
    finally:
        tmp.unlink(missing_ok=True)
        if asset is not None:
            try:
                storage.delete(asset.key)
                steps["cleanup"] = True
                gone, _ = storage.verify_public(asset.url)
                steps["cleanup_effective"] = not gone
            except ClipyError as exc:
                steps["cleanup"] = False
                steps["cleanup_detail"] = exc.message
    if not steps.get("anonymous_https"):
        return TEST_FAILED, f"Upload riuscito ma l'URL non è accessibile anonimamente via HTTPS: {steps.get('anonymous_https_detail')}", steps
    if steps.get("cleanup") is False or steps.get("cleanup_effective") is False:
        return TEST_FAILED, "Upload e accesso OK ma la cancellazione del file di prova non è andata a buon fine: ripulisci il bucket.", steps
    return CONNECTED, "Upload, accesso anonimo HTTPS e cancellazione verificati.", steps


def _t_llm(db: Session) -> tuple[str, str, dict]:
    st = ss.get_settings(db)
    a = st.ai
    if a.provider == "mock":
        return NOT_CONFIGURED, "Analisi SIMULATA: nessun provider configurato.", {}
    an = factory.analyzer(db, st, False)
    t0 = time.monotonic()
    text, tin, tout = an._call("Rispondi solo con JSON.", 'Restituisci esattamente {"ok": true}', [])  # type: ignore[attr-defined]
    ms = int((time.monotonic() - t0) * 1000)
    d = {"provider": a.provider, "model": a.model, "latency_ms": ms, "tokens_in": tin, "tokens_out": tout, "vision_enabled": a.vision_enabled}
    if "ok" not in text.lower():
        return TEST_FAILED, "Il provider risponde ma l'output non è nel formato richiesto.", d
    return CONNECTED, f"Provider raggiunto in {ms} ms ({tin}+{tout} token). La qualità dei titoli si valuta su clip reali.", d


def _t_whisper(db: Session) -> tuple[str, str, dict]:
    import wave

    st = ss.get_settings(db)
    h = asr_health(st.ai.whisper_model, st.ai.whisper_device)
    if st.ai.transcription == "off":
        return NOT_CONFIGURED, "Trascrizione disattivata nelle impostazioni.", h
    if not h["faster_whisper_installed"]:
        return NOT_CONFIGURED, "faster-whisper non installato: nessuna trascrizione verrà inventata, le clip andranno in revisione.", h
    t = factory.transcriber(db, st, False)
    model_t0 = time.monotonic()
    try:
        model = t._load()  # type: ignore[attr-defined]
    except Exception as exc:
        return TEST_FAILED, f"Modello non caricabile: {type(exc).__name__}: {exc}"[:300], h
    wav = get_env().dir("temp") / f"whisper-test-{uuid.uuid4().hex[:6]}.wav"
    try:
        with wave.open(str(wav), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(b"\x00\x00" * 16000)
        segs, _ = model.transcribe(str(wav), language=st.ai.language, vad_filter=True, beam_size=1)
        list(segs)
    except Exception as exc:
        return TEST_FAILED, f"Trascrizione di prova fallita: {type(exc).__name__}: {exc}"[:300], h
    finally:
        wav.unlink(missing_ok=True)
    h["load_and_run_ms"] = int((time.monotonic() - model_t0) * 1000)
    warn = h.get("warnings") or []
    return CONNECTED, "Modello caricato e trascrizione di prova eseguita." + (" Avvisi: " + " ".join(warn) if warn else ""), h


def _t_ffmpeg(db: Session) -> tuple[str, str, dict]:
    ff, fp = ffmpeg.have_binary("ffmpeg"), ffmpeg.have_binary("ffprobe")
    if not (ff and fp):
        return NOT_CONFIGURED, "ffmpeg/ffprobe non trovati (PATH, cartelle Windows comuni, CLIPY_FFMPEG_DIR).", {"ffmpeg": bool(ff), "ffprobe": bool(fp)}
    ver = ffmpeg.run(["ffmpeg", "-version"], 20).stdout.splitlines()[0]
    filters = ffmpeg.run(["ffmpeg", "-hide_banner", "-filters"], 20).stdout
    d = {"version": ver, "drawtext": "drawtext" in filters}
    tmp = get_env().dir("temp") / f"ffmpeg-test-{uuid.uuid4().hex[:6]}.mp4"
    try:
        ffmpeg.make_fixture(tmp, width=360, height=640, seconds=1, audio=True, label="clipy-ffmpeg-test")
        info = ffmpeg.probe(tmp)
        d["h264_aac_encode"] = info["vcodec"] == "h264" and info["acodec"] == "aac"
    except ClipyError as exc:
        return TEST_FAILED, f"Codifica di prova fallita: {exc.message}", d
    finally:
        tmp.unlink(missing_ok=True)
    if not d["drawtext"]:
        return TEST_FAILED, "Questo FFmpeg non ha il filtro drawtext: installa la build 'full' (Gyan.FFmpeg).", d
    if not d["h264_aac_encode"]:
        return TEST_FAILED, "Codifica H.264/AAC non disponibile.", d
    return CONNECTED, "ffmpeg/ffprobe OK: drawtext e codifica H.264/AAC verificati.", d


_TESTS = {"twitch": _t_twitch, "buffer": _t_buffer, "storage": _t_storage, "llm": _t_llm, "whisper": _t_whisper, "ffmpeg": _t_ffmpeg}


def run_test(db: Session, service: str, *, confirm: bool = False) -> dict:
    if service not in _TESTS:
        raise ClipyError("Servizio sconosciuto", code="unknown_service", status_code=404)
    if service in NEEDS_CONFIRM and not confirm:
        raise ClipyError(f"Test non eseguito: richiede conferma esplicita. {NEEDS_CONFIRM[service]}", code="confirmation_required", status_code=422)
    base_state, simulated, _, _ = _configured(db, service)
    if base_state == NOT_CONFIGURED and not simulated and service not in ("ffmpeg", "whisper"):
        raise ClipyError("Servizio non configurato: completa prima la configurazione.", code="not_configured", status_code=409)
    t0 = time.monotonic()
    try:
        state, message, details = _TESTS[service](db)
        ok = state in (CONNECTED, NEEDS_LIVE)
    except ClipyError as exc:
        state, message, details, ok = TEST_FAILED, exc.message, {"code": exc.code}, False
    except Exception as exc:  # errore imprevisto: mai un successo di facciata
        state, message, details, ok = TEST_FAILED, f"Errore imprevisto: {type(exc).__name__}: {exc}"[:300], {}, False
    sim = state == NOT_CONFIGURED and simulated
    _store(db, service, {"state": state, "ok": ok, "message": message, "details": _clean(details), "at": iso(utcnow()), "duration_ms": int((time.monotonic() - t0) * 1000),
                         "fingerprint": _fingerprint(db, service), "simulated": sim})
    return entry(db, service)


def _clean(d: dict) -> dict:
    """Difesa in profondità: niente valori che somiglino a segreti nei dettagli persistiti."""
    bad = ("key", "secret", "token", "password")
    return {k: ("***" if any(b in k.lower() for b in bad) and isinstance(v, str) else v) for k, v in d.items()}



