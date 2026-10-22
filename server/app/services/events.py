from __future__ import annotations

import logging
import re

from sqlalchemy.orm import Session

from app.db.models import Event, StateTransition

log = logging.getLogger("clipy")
_SECRET_RE = re.compile(r"(?i)(bearer\s+|oauth\s+|api[_-]?key[=:]\s*|secret[=:]\s*|access_token[=:]\s*|refresh_token[=:]\s*|client_secret=|authorization:\s*)[A-Za-z0-9._\-]{8,}")
_QUERY_RE = re.compile(r"(?i)([?&](?:code|state|access_token|refresh_token|client_secret|api_key|token)=)[^&\s\"']+")
_TOKEN_RE = re.compile(r"\b(?:sk-[A-Za-z0-9_\-]{16,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{20,})\b")


def sanitize(msg: str) -> str:
    """Maschera token, chiavi API e parametri OAuth (anche negli URL degli access log)."""
    msg = _SECRET_RE.sub(lambda m: m.group(1) + "***", msg)
    msg = _QUERY_RE.sub(lambda m: m.group(1) + "***", msg)
    return _TOKEN_RE.sub("***", msg)[:2000]


class SecretFilter(logging.Filter):
    """Filtro applicato a TUTTI i log (anche uvicorn.access): nessun segreto raggiunge console o file."""

    def filter(self, record: logging.LogRecord) -> bool:
        # La STRUTTURA di msg/args resta intatta (il formatter di uvicorn.access spacchetta args): si sanificano i singoli valori.
        try:
            if isinstance(record.msg, str):
                record.msg = sanitize(record.msg)
            if isinstance(record.args, tuple):
                record.args = tuple(sanitize(a) if isinstance(a, str) else a for a in record.args)
            elif isinstance(record.args, dict):
                record.args = {k: sanitize(v) if isinstance(v, str) else v for k, v in record.args.items()}
        except Exception:  # un log non deve mai far fallire l'app
            pass
        return True


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
    from app.domain.states import is_legal

    if not is_legal(entity, old, new_state):  # non blocca (nessuna clip bloccata da un controllo): lascia traccia
        emit(db, "state-machine", "illegal_transition", f"{entity} {obj.id}: {old} → {new_state} non previsto", severity="warning")
    obj.status = new_state
    db.add(StateTransition(entity=entity, entity_id=obj.id, from_state=old, to_state=new_state, note=note and sanitize(note)))
    db.flush()
