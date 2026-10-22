"""Lease transazionali: leader del worker e lock per-riga di clip/job."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.timeutil import utcnow
from app.db.models import Lease


def acquire_leader(db: Session, name: str, owner: str, ttl_seconds: int = 60) -> bool:
    now = utcnow()
    exp = now + timedelta(seconds=ttl_seconds)
    row = db.get(Lease, name)
    if row is None:
        try:
            db.add(Lease(name=name, owner=owner, expires_at=exp))
            db.flush()
            return True
        except Exception:
            db.rollback()
            return False
    res = db.execute(
        update(Lease).where(Lease.name == name, (Lease.owner == owner) | (Lease.expires_at < now)).values(owner=owner, expires_at=exp)
    )
    return res.rowcount == 1


def lock_row(db: Session, model, row_id: str, seconds: int = 600) -> bool:
    """Acquisisce atomicamente `locked_until` su una riga (clip o publication job)."""
    now = utcnow()
    res = db.execute(
        update(model)
        .where(model.id == row_id, (model.locked_until.is_(None)) | (model.locked_until < now))
        .values(locked_until=now + timedelta(seconds=seconds))
    )
    db.commit()
    return res.rowcount == 1


def unlock_row(db: Session, model, row_id: str) -> None:
    db.execute(update(model).where(model.id == row_id).values(locked_until=None))
    db.commit()
