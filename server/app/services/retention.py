"""Retention dei file media: elimina originali/render/anteprime di clip concluse oltre `retention_days`.
Le righe DB e l'audit restano; i file di clip ancora in pipeline/coda non vengono mai toccati."""
from __future__ import annotations

import time
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_env
from app.core.timeutil import utcnow
from app.db.models import Clip, PublicationJob
from app.domain import states as S
from app.services import events
from app.services import settings_service as ss


def cleanup(db: Session) -> dict:
    st = ss.get_settings(db)
    cutoff = utcnow() - timedelta(days=st.system.retention_days)
    busy_clips = {j.clip_id for j in db.scalars(select(PublicationJob).where(PublicationJob.status.in_(S.PUB_OPEN)))}
    freed = removed = 0
    for c in db.scalars(select(Clip).where(Clip.status.in_([S.REJECTED, S.SKIPPED, S.APPROVED, S.AUTO_READY]), Clip.updated_at < cutoff)):
        published = db.scalar(select(PublicationJob.id).where(PublicationJob.clip_id == c.id, PublicationJob.status == S.PUBLISHED))
        if c.id in busy_clips or (c.status in (S.APPROVED, S.AUTO_READY) and not published):
            continue
        for attr in ("original_path", "render_path"):
            p = getattr(c, attr)
            if p and Path(p).exists():
                size = Path(p).stat().st_size
                try:
                    Path(p).unlink()
                except OSError as exc:  # Windows: file ancora aperto da uno streaming in corso (WinError 32) → riprova al prossimo ciclo
                    events.emit(db, "retention", "file_in_use", f"File in uso, rimozione rinviata: {Path(p).name} ({type(exc).__name__})", severity="warning", clip_id=c.id)
                    continue
                freed += size
                removed += 1
            setattr(c, attr, None)
    env = get_env()
    for sub in ("temp", "previews"):  # file temporanei/anteprime più vecchi di 2 giorni
        for f in env.dir(sub).glob("*"):
            try:
                if f.is_file() and time.time() - f.stat().st_mtime > 2 * 86400:
                    f.unlink(missing_ok=True)
            except OSError:  # file in uso su Windows: lo rimuove il prossimo ciclo
                continue
    from app.services import housekeeping

    housekeeping.cleanup_stale_files()  # parziali orfani (.part) di download/render interrotti
    if removed:
        events.emit(db, "retention", "cleanup", f"Retention: {removed} file rimossi ({freed // 1024 // 1024} MB)")
    return {"removed": removed, "freed_bytes": freed}
