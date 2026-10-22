from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends
from sqlalchemy.orm import Session

from app.db.base import get_db


def db_dep() -> Iterator[Session]:
    yield from get_db()


DB = Depends(db_dep)
