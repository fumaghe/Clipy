from __future__ import annotations

import logging
import re

from sqlalchemy.orm import Session

from app.db.models import Event, StateTransition

log = logging.getLogger("clipy")
_SECRET_RE = re.compile(r"(?i)(bearer\s+|api[_-]?key[=:]\s*|secret[=:]\s*|access_token[=:]\s*|client_secret=)[A-Za-z0-9._\-]{8,}")


def sanitize(msg: str) -> str:
    return _SECRET_RE.sub(lambda m: m.group(1) + "***", msg)[:2000]


def emit(db: Session, component: str, type_: str, message: str, *, severity: str = "info",
         clip_id: str | None = None, job_id: str | None = None) -> None:
    msg = sanitize(message)
    db.add(Event(component=component, type=type_, severity=severity, clip_id=clip_id, job_id=job_id, message=msg))
    log.log(logging.ERROR if severity == "error" else logging.INFO, "[%s/%s] %s", component, type_, msg)
    db.flush()


def transition(db: Session, entity: str, obj, new_state: str, note: str | None = None) -> None:
    old = obj.status
    if old == new_state and not note:
        return
    obj.status = new_state
    db.add(StateTransition(entity=entity, entity_id=obj.id, from_state=old, to_state=new_state, note=note and sanitize(note)))
    db.flush()
