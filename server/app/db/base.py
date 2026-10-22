from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_env


class Base(DeclarativeBase):
    pass


_lock = threading.Lock()
_engine: Engine | None = None
_factory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine, _factory
    with _lock:
        if _engine is None:
            url = get_env().db_url
            kw = {"connect_args": {"check_same_thread": False, "timeout": 30}} if url.startswith("sqlite") else {}
            _engine = create_engine(url, **kw)
            if url.startswith("sqlite"):

                @event.listens_for(_engine, "connect")
                def _pragmas(dbapi, _):  # noqa: ANN001
                    cur = dbapi.cursor()
                    cur.execute("PRAGMA journal_mode=WAL")
                    cur.execute("PRAGMA foreign_keys=ON")
                    cur.execute("PRAGMA busy_timeout=30000")
                    cur.close()

            _factory = sessionmaker(_engine, expire_on_commit=False)
        return _engine


def reset_engine() -> None:
    global _engine, _factory
    with _lock:
        if _engine is not None:
            _engine.dispose()
        _engine = None
        _factory = None


def new_session() -> Session:
    get_engine()
    assert _factory is not None
    return _factory()


@contextmanager
def session_scope() -> Iterator[Session]:
    s = new_session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_db() -> Iterator[Session]:
    s = new_session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def run_migrations() -> None:
    from pathlib import Path

    from alembic.config import Config

    from alembic import command

    root = Path(__file__).resolve().parents[2]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", get_env().db_url.replace("%", "%%"))
    command.upgrade(cfg, "head")
