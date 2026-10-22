from __future__ import annotations

import re
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.twitch.base import ClipMeta
from app.core.errors import ClipyError, TemporaryError
from app.core.timeutil import utcnow
from app.db.models import Clip, SyncCursor
from app.domain import states as S
from app.services import events, factory
from app.services import settings_service as ss

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,128}$")
_URL_RE = re.compile(r"(?:clips\.twitch\.tv/|twitch\.tv/[^/\s]+/clip/)([A-Za-z0-9_-]+)")


def parse_clip_ref(ref: str) -> str:
    ref = ref.strip()
    m = _URL_RE.search(ref)
    cid = m.group(1) if m else ref
    if not _ID_RE.match(cid):
        raise ClipyError("ID o URL clip Twitch non valido", code="invalid_clip_ref", status_code=422)
    return cid  # solo l'ID viene usato: nessuna richiesta verso URL arbitrari (no SSRF)


def upsert_clip(db: Session, m: ClipMeta, *, demo: bool) -> tuple[Clip, bool]:
    existing = db.scalar(select(Clip).where(Clip.source_clip_id == m.id))
    if existing:
        return existing, False
    c = Clip(source_clip_id=m.id, broadcaster_id=m.broadcaster_id, broadcaster_name=m.broadcaster_name, creator_id=m.creator_id,
             creator_name=m.creator_name, game_id=m.game_id, game_name=m.game_name, twitch_url=m.url, title=m.title,
             created_at_utc=m.created_at, duration=m.duration, view_count=m.view_count, thumbnail_url=m.thumbnail_url,
             is_demo=demo, demo_scenario=m.extra.get("scenario", ""), status=S.DISCOVERED)
    c.media_info = {"demo_fixture": m.extra.get("fixture")} if demo else {}
    db.add(c)
    db.flush()
    events.emit(db, "collector", "clip_discovered", f"Nuova clip {m.id} da {m.creator_name or '?'}", clip_id=c.id)
    return c, True


def scan(db: Session, *, now=None) -> dict:
    """Una scansione Twitch: finestra con overlap, paginazione completa, upsert idempotente."""
    now = now or utcnow()
    st = ss.get_settings(db)
    tw = st.twitch
    login = tw.login.lstrip("@")
    cur = db.get(SyncCursor, login.lower())
    first_run = cur is None or cur.last_successful_scan_at is None
    if cur is None:
        cur = SyncCursor(broadcaster_login=login.lower())
        db.add(cur)
    try:
        source = factory.clip_source(db, st)
        if not cur.broadcaster_id:
            cur.broadcaster_id = source.resolve_user(login).id  # non indovinare il broadcaster_id
        start = now - timedelta(hours=tw.bootstrap_hours) if first_run else cur.last_successful_scan_at - timedelta(hours=tw.overlap_hours)
        metas = source.list_clips(cur.broadcaster_id, start, now)
        demo = bool(getattr(source, "simulated", False))
        new = 0
        for m in metas:
            if not tw.collect_all_channel_clips and m.creator_name.lower() != login.lower():
                continue
            _, created = upsert_clip(db, m, demo=demo)
            new += int(created)
        cur.last_successful_scan_at, cur.last_window_start = now, start
        cur.last_scan_found, cur.last_scan_new, cur.last_error = len(metas), new, None
        bad = (getattr(source, "last_stats", None) or {}).get("malformed", 0)
        events.emit(db, "collector", "scan_ok", f"Scansione {'iniziale (backfill)' if first_run else 'ricorrente'}: {len(metas)} clip nella finestra, {new} nuove"
                    + (f", {bad} voci malformate ignorate" if bad else "") + (" [DEMO simulato]" if demo else ""), severity="warning" if bad else "info")
        db.commit()
        return {"found": len(metas), "new": new, "first_run": first_run, "simulated": demo}
    except (ClipyError, TemporaryError) as exc:
        db.rollback()
        cur = db.get(SyncCursor, login.lower()) or SyncCursor(broadcaster_login=login.lower())
        cur.last_error = exc.message if isinstance(exc, ClipyError) else str(exc)
        db.add(cur)
        events.emit(db, "collector", "scan_failed", f"Scansione fallita: {cur.last_error}", severity="error")
        db.commit()
        raise  # il cursore NON avanza: nessuna clip persa


def import_clip(db: Session, ref: str) -> Clip:
    cid = parse_clip_ref(ref)
    st = ss.get_settings(db)
    source = factory.clip_source(db, st)
    m = source.get_clip(cid)
    if m is None:
        raise ClipyError("Clip non trovata su Twitch", code="clip_not_found", status_code=404)
    c, _ = upsert_clip(db, m, demo=bool(getattr(source, "simulated", False)))
    db.commit()
    return c
