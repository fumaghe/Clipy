from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

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
        return row.value
    return str(getattr(get_env(), name, "") or "")


def set_secret(db: Session, name: str, value: str) -> None:
    if name not in SECRET_NAMES:
        raise ClipyError(f"Segreto sconosciuto: {name}", code="unknown_secret", status_code=422)
    row = db.get(Secret, name)
    if not value:
        if row:
            db.delete(row)
    elif row:
        row.value = value
    else:
        db.add(Secret(name=name, value=value))
    db.flush()


def secrets_status(db: Session) -> dict[str, bool]:
    return {n: bool(get_secret(db, n)) for n in sorted(SECRET_NAMES)}
