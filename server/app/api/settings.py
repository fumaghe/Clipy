from __future__ import annotations

import uuid
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.adapters.twitch.helix import OAUTH, REQUIRED_SCOPE, SCOPES
from app.adapters.video import ffmpeg
from app.api.deps import DB
from app.core.config import get_env
from app.core.errors import ClipyError, PermanentError
from app.core.settings_model import SECRET_NAMES
from app.core.timeutil import utcnow
from app.db.models import OAuthState
from app.services import events, factory
from app.services import settings_service as ss
from app.workers import runner

router = APIRouter(prefix="/api/v1", tags=["settings"])
LIVE_CONFIRM_PHRASE = "ABILITA PUBBLICAZIONE REALE"


class SecretBody(BaseModel):
    name: str
    value: str = Field(max_length=2000)


class LiveBody(BaseModel):
    confirm: str


class BufferSelect(BaseModel):
    channel_id: str = Field(min_length=1, max_length=200)


@router.get("/settings")
def get_settings(db: Session = DB) -> dict:
    return {"settings": ss.get_settings(db).model_dump(), "secrets": ss.secrets_status(db), "version": ss.settings_version(db),
            "live_confirm_phrase": LIVE_CONFIRM_PHRASE, "redirect_uri": get_env().twitch_redirect_uri}


@router.patch("/settings")
def patch_settings(patch: dict, db: Session = DB) -> dict:
    # live_enabled e is_connected si cambiano solo con gli endpoint dedicati
    pub = patch.get("publishing") or {}
    for forbidden in ("live_enabled", "is_connected"):
        if forbidden in pub:
            raise ClipyError(f"'{forbidden}' si modifica solo dal flusso dedicato", code="forbidden_field", status_code=422)
    ss.update_settings(db, patch)
    events.emit(db, "settings", "updated", "Impostazioni aggiornate")
    return get_settings(db)


@router.put("/settings/secrets")
def put_secret(body: SecretBody, db: Session = DB) -> dict:
    if body.name not in SECRET_NAMES:
        raise ClipyError("Segreto sconosciuto", code="unknown_secret", status_code=422)
    ss.set_secret(db, body.name, body.value.strip())
    if body.name == "twitch_client_id" and body.value.strip():
        ss.update_settings(db, {"system": {"demo_mode": False}})
    if body.name == "buffer_api_key":
        # un'altra chiave = un altro account/canale potenziale: scollega e torna in DRY RUN finché non si ritesta e si riseleziona
        from app.db.models import Setting

        row = db.get(Setting, "product")
        if row is not None:
            row.value = {**row.value, "publishing": {**row.value["publishing"], "is_connected": False, "channel_id": "", "organization_id": "",
                                                       "live_enabled": False, "dry_run": True}}
    events.emit(db, "settings", "secret_updated", f"Segreto '{body.name}' aggiornato")  # il valore non viene mai loggato
    return {"secrets": ss.secrets_status(db)}


@router.post("/settings/live/enable")
def live_enable(body: LiveBody, db: Session = DB) -> dict:
    st = ss.get_settings(db)
    if body.confirm.strip() != LIVE_CONFIRM_PHRASE:
        raise ClipyError(f"Conferma errata: digita esattamente «{LIVE_CONFIRM_PHRASE}»", code="bad_confirmation", status_code=422)
    p = st.publishing
    if p.provider != "buffer" or not p.is_connected or not p.channel_id:
        raise ClipyError("Collega prima Buffer (wizard) e seleziona il canale TikTok", code="buffer_not_connected", status_code=409)
    if st.storage.provider != "s3":
        raise ClipyError("Configura lo storage S3/R2 con URL pubblico: Buffer non carica file locali", code="storage_not_configured", status_code=409)
    ss.update_settings(db, {})
    from app.db.models import Setting

    row = db.get(Setting, "product")
    assert row is not None
    row.value = {**row.value, "publishing": {**row.value["publishing"], "live_enabled": True, "dry_run": False}}
    events.emit(db, "settings", "live_enabled", "MODALITÀ LIVE ABILITATA dal proprietario", severity="warning")
    return get_settings(db)


@router.post("/settings/live/disable")
def live_disable(db: Session = DB) -> dict:
    from app.db.models import Setting

    ss.get_settings(db)
    row = db.get(Setting, "product")
    assert row is not None
    row.value = {**row.value, "publishing": {**row.value["publishing"], "live_enabled": False, "dry_run": True}}
    events.emit(db, "settings", "live_disabled", "Modalità live disattivata: DRY RUN attivo", severity="warning")
    return get_settings(db)


# ---------------- Twitch OAuth
@router.post("/integrations/twitch/connect")
def twitch_connect(db: Session = DB) -> dict:
    cid = ss.get_secret(db, "twitch_client_id")
    if not cid or not ss.get_secret(db, "twitch_client_secret"):
        raise ClipyError("Inserisci Client ID e Client Secret Twitch prima di collegare l'account", code="twitch_not_configured", status_code=409)
    db.query(OAuthState).filter(OAuthState.created_at < utcnow() - timedelta(minutes=15)).delete()  # pulizia stati scaduti
    state = uuid.uuid4().hex
    db.add(OAuthState(state=state))
    db.flush()
    q = urlencode({"client_id": cid, "redirect_uri": get_env().twitch_redirect_uri, "response_type": "code", "scope": " ".join(SCOPES), "state": state, "force_verify": "true"})
    return {"authorize_url": f"{OAUTH}/authorize?{q}", "scopes": SCOPES}


@router.get("/integrations/twitch/callback")
def twitch_callback(code: str = "", state: str = "", error: str = "", db: Session = DB):
    """Authorization Code: `state` monouso e a scadenza (CSRF), scope e identità verificati prima di salvare i token."""
    row = db.get(OAuthState, state) if state else None
    if row is not None:
        db.delete(row)  # monouso, anche in caso di errore
        db.flush()
    expired = row is not None and (utcnow() - row.created_at).total_seconds() > 600
    if error or row is None or expired or not code:
        events.emit(db, "twitch", "oauth_rejected", "Callback OAuth Twitch rifiutato (state non valido/scaduto, o autorizzazione negata)", severity="warning")
        return RedirectResponse("/settings?twitch=error")
    try:
        with httpx.Client(timeout=20) as c:
            r = c.post(f"{OAUTH}/token", data={"client_id": ss.get_secret(db, "twitch_client_id"), "client_secret": ss.get_secret(db, "twitch_client_secret"),
                                               "code": code, "grant_type": "authorization_code", "redirect_uri": get_env().twitch_redirect_uri})
            if r.status_code != 200:
                events.emit(db, "twitch", "oauth_failed", f"Scambio del codice OAuth fallito (HTTP {r.status_code})", severity="warning")
                return RedirectResponse("/settings?twitch=error")
            j = r.json()
            vr = c.get(f"{OAUTH}/validate", headers={"Authorization": f"OAuth {j['access_token']}"})
            v = vr.json() if vr.status_code == 200 else {}
    except (httpx.HTTPError, ValueError, KeyError):
        return RedirectResponse("/settings?twitch=error")
    scopes = list(v.get("scopes") or j.get("scope") or [])
    if REQUIRED_SCOPE not in scopes:
        events.emit(db, "twitch", "oauth_scope_missing", f"Autorizzazione senza lo scope {REQUIRED_SCOPE}: token NON salvato", severity="warning")
        return RedirectResponse("/settings?twitch=scope")
    ss.set_secret(db, "twitch_access_token", j["access_token"])
    ss.set_secret(db, "twitch_refresh_token", j.get("refresh_token", ""))
    ss.update_settings(db, {"system": {"demo_mode": False}})
    login = str(v.get("login", ""))
    configured = ss.get_settings(db).twitch.login.lstrip("@").lower()
    ss.set_twitch_auth(db, {"user_id": str(v.get("user_id", "")), "login": login, "scopes": scopes, "authorized_at": utcnow().isoformat()})
    same = login.lower() == configured
    events.emit(db, "twitch", "authorized", f"Twitch autorizzato come {login or '?'} (scope: {', '.join(scopes)})"
                + ("" if same else f". ATTENZIONE: il canale configurato è {configured}: serve che {login} sia editor di quel canale"),
                severity="info" if same else "warning")
    return RedirectResponse("/settings?twitch=ok" if same else "/settings?twitch=not_owner")


@router.post("/integrations/twitch/test")
def twitch_test(db: Session = DB) -> dict:
    st = ss.get_settings(db)
    src = factory.clip_source(db, st)
    u = src.resolve_user(st.twitch.login)
    return {"ok": True, "simulated": bool(getattr(src, "simulated", False)), "login": u.login, "broadcaster_id": u.id}


@router.post("/integrations/buffer/test")
def buffer_test(db: Session = DB) -> dict:
    st = ss.get_settings(db)
    pub = factory.publisher(db, st)
    res = pub.test_connection()
    events.emit(db, "buffer", "test", f"Test Buffer OK: {len(res.get('channels', []))} canali" + (" [SIMULATO]" if res.get("simulated") else ""))
    return res


@router.post("/integrations/buffer/select")
def buffer_select(body: BufferSelect, db: Session = DB) -> dict:
    st = ss.get_settings(db)
    pub = factory.publisher(db, st)
    chans = pub.test_connection().get("channels", [])
    ch = next((c for c in chans if c["id"] == body.channel_id), None)
    if ch is None:
        raise ClipyError("Canale non trovato nell'account Buffer", code="channel_not_found", status_code=404)
    if ch.get("disconnected"):
        raise ClipyError("Il canale risulta disconnesso su Buffer: ricollegalo dal pannello Buffer", code="channel_disconnected", status_code=409)
    if ch.get("service") and ch["service"].lower() != "tiktok":
        raise ClipyError("Il canale selezionato non è TikTok", code="not_tiktok", status_code=409)
    if not ch.get("organization_id"):
        raise ClipyError("Organizzazione Buffer del canale sconosciuta", code="organization_missing", status_code=409)
    from app.db.models import Setting

    ss.get_settings(db)
    row = db.get(Setting, "product")
    assert row is not None
    row.value = {**row.value, "publishing": {**row.value["publishing"], "channel_id": body.channel_id, "is_connected": True,
                                                 "organization_id": ch.get("organization_id", "")}}
    events.emit(db, "buffer", "channel_selected", f"Canale TikTok selezionato: {ch.get('name')}")
    return get_settings(db)


@router.post("/integrations/storage/test")
def storage_test(db: Session = DB) -> dict:
    st = ss.get_settings(db)
    live = st.storage.provider == "s3"
    storage = factory.storage(db, st, for_live=live)
    tmp = get_env().dir("temp") / f"storage-test-{uuid.uuid4().hex[:6]}.mp4"
    ffmpeg.make_fixture(tmp, width=360, height=640, seconds=1, audio=False, label="storage-test")
    try:
        asset = storage.upload(Path(tmp), f"test-{uuid.uuid4().hex}.mp4")
        ok, why = storage.verify_public(asset.url)
        storage.delete(asset.key)
    finally:
        tmp.unlink(missing_ok=True)
    if not live:
        return {"ok": ok, "public": False, "message": "Storage LOCALE (mock): non pubblico, valido solo per demo/dry-run. " + why}
    if not ok:
        raise PermanentError(f"URL pubblico non verificato: {why}", code="storage_unreachable")
    return {"ok": True, "public": True, "message": "Upload, accesso anonimo e cancellazione verificati"}


@router.post("/system/worker/{action}")
def worker_toggle(action: str) -> dict:
    if action == "start":
        runner.start()
    elif action == "stop":
        runner.stop()
    return {"running": runner.is_running()}
