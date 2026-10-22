from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core import secretbox
from app.core.config import get_env
from app.core.errors import ClipyError
from app.core.settings_model import SECRET_NAMES, ProductSettings
from app.db.models import Secret, Setting


def _merge(base: dict, patch: dict) -> dict:
    out = dict(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def get_settings(db: Session) -> ProductSettings:
    row = db.get(Setting, "product")
    if row is None:
        s = ProductSettings()
        # senza credenziali Twitch → modalità demo
        s.system.demo_mode = not bool(get_secret(db, "twitch_client_id"))
        db.add(Setting(key="product", value=s.model_dump(), version=1))
        db.flush()
        return s
    return ProductSettings.model_validate(row.value)


def update_settings(db: Session, patch: dict[str, Any]) -> ProductSettings:
    current = get_settings(db)
    merged = _merge(current.model_dump(), patch)
    try:
        new = ProductSettings.model_validate(merged)
    except Exception as exc:  # pydantic ValidationError → messaggio leggibile
        raise ClipyError(f"Impostazioni non valide: {exc}", code="invalid_settings", status_code=422) from exc
    row = db.get(Setting, "product")
    assert row is not None
    row.value = new.model_dump()
    row.version = (row.version or 1) + 1
    db.flush()
    return new


def settings_version(db: Session) -> int:
    row = db.get(Setting, "product")
    return row.version if row else 1


def get_secret(db: Session, name: str) -> str:
    if name not in SECRET_NAMES:
        raise ClipyError(f"Segreto sconosciuto: {name}", code="unknown_secret")
    row = db.get(Secret, name)
    if row and row.value:
        try:
            return secretbox.unprotect(row.value)
        except secretbox.Unreadable:
            return ""  # trattato come non configurato: l'utente deve reinserirlo (vedi secret_unreadable)
    return str(getattr(get_env(), name, "") or "")


def secret_unreadable(db: Session, name: str) -> bool:
    row = db.get(Secret, name)
    if not (row and row.value and secretbox.is_protected(row.value)):
        return False
    try:
        secretbox.unprotect(row.value)
        return False
    except secretbox.Unreadable:
        return True


def protect_all(db: Session) -> int:
    """Cifra i segreti ancora in chiaro (se la protezione è attiva). Il valore non cambia: migrazione senza perdita."""
    if secretbox.mode() != "dpapi":
        return 0
    n = 0
    for row in db.query(Secret).all():
        if row.value and not secretbox.is_protected(row.value):
            row.value = secretbox.protect(row.value)
            n += 1
    db.flush()
    return n


def unprotect_all(db: Session) -> int:
    """Operazione inversa (es. per migrare a un altro host/secret manager). Richiede lo stesso utente Windows."""
    n = 0
    for row in db.query(Secret).all():
        if row.value and secretbox.is_protected(row.value):
            row.value = secretbox.unprotect(row.value)
            n += 1
    db.flush()
    return n


def set_secret(db: Session, name: str, value: str) -> None:
    if name not in SECRET_NAMES:
        raise ClipyError(f"Segreto sconosciuto: {name}", code="unknown_secret", status_code=422)
    row = db.get(Secret, name)
    if not value:
        if row:
            db.delete(row)
    elif row:
        row.value = secretbox.protect(value)
    else:
        db.add(Secret(name=name, value=secretbox.protect(value)))
    db.flush()


def secrets_status(db: Session) -> dict[str, bool]:
    return {n: bool(get_secret(db, n)) for n in sorted(SECRET_NAMES)}


def get_twitch_auth(db: Session) -> dict:
    """Identità dell'utente che ha autorizzato Twitch (non segreta: id, login, scope)."""
    row = db.get(Setting, "twitch_auth")
    return dict(row.value) if row else {}


def set_twitch_auth(db: Session, value: dict) -> None:
    row = db.get(Setting, "twitch_auth")
    if row is None:
        db.add(Setting(key="twitch_auth", value=value, version=1))
    else:
        row.value = value
    db.flush()
