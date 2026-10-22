"""Worker: un solo leader (lease nel DB) esegue collector, pipeline e publisher. Mai due volte con il reload."""
from __future__ import annotations

import logging
import os
import threading
import uuid
from datetime import timedelta

from apscheduler.schedulers.background import BackgroundScheduler

from app.core.config import get_env
from app.core.errors import ClipyError, TemporaryError
from app.core.timeutil import utcnow
from app.db.base import new_session
from app.db.models import SyncCursor
from app.services import collector, leases, pipeline, publishing
from app.services import settings_service as ss

log = logging.getLogger("clipy.worker")
OWNER = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
_sched: BackgroundScheduler | None = None
_proc_lock = threading.Lock()


_was_leader = False


def _leader(db) -> bool:
    """Lease unico nel DB. Alla prima acquisizione di questo processo (dopo un crash/riavvio) recupera i lock orfani."""
    global _was_leader
    ok = leases.acquire_leader(db, "worker", OWNER, 60)
    if ok and not _was_leader:
        leases.recover_locks(db)
    _was_leader = ok
    db.commit()
    return ok


def collector_tick() -> None:
    db = new_session()
    try:
        if not _leader(db):
            return
        st = ss.get_settings(db)
        if not st.system.collector_enabled:
            return
        cur = db.get(SyncCursor, st.twitch.login.lstrip("@").lower())
        if cur and cur.last_successful_scan_at and utcnow() - cur.last_successful_scan_at < timedelta(minutes=st.twitch.polling_minutes):
            return
        try:
            collector.scan(db)
        except (ClipyError, TemporaryError) as exc:
            log.warning("scan fallita: %s", exc)
    except Exception:
        log.exception("collector_tick")
    finally:
        db.close()


def pipeline_tick() -> None:
    if not _proc_lock.acquire(blocking=False):
        return
    db = new_session()
    try:
        if _leader(db):
            pipeline.run_until_idle(db, max_steps=20)
    except Exception:
        log.exception("pipeline_tick")
    finally:
        db.close()
        _proc_lock.release()


def publisher_tick() -> None:
    db = new_session()
    try:
        if _leader(db):
            publishing.tick(db)
    except Exception:
        log.exception("publisher_tick")
    finally:
        db.close()


def retention_tick() -> None:
    from app.services import retention

    db = new_session()
    try:
        if _leader(db):
            retention.cleanup(db)
            db.commit()
    except Exception:
        log.exception("retention_tick")
    finally:
        db.close()


def start() -> None:
    global _sched
    if _sched is not None:
        return
    env = get_env()
    _sched = BackgroundScheduler(timezone="UTC", job_defaults={"max_instances": 1, "coalesce": True})
    _sched.add_job(collector_tick, "interval", seconds=30, id="collector", next_run_time=utcnow() + timedelta(seconds=3))
    _sched.add_job(pipeline_tick, "interval", seconds=env.pipeline_tick_seconds, id="pipeline")
    _sched.add_job(publisher_tick, "interval", seconds=env.publisher_tick_seconds, id="publisher")
    _sched.add_job(retention_tick, "interval", hours=6, id="retention", next_run_time=utcnow() + timedelta(minutes=2))
    _sched.start()
    log.info("worker avviato (owner %s)", OWNER)


def stop() -> None:
    global _sched
    if _sched:
        _sched.shutdown(wait=False)
        _sched = None


def is_running() -> bool:
    return _sched is not None


def process_in_background() -> bool:
    """Avvia run_until_idle in un thread (bottone 'Run once'). False se già in corso."""
    if _proc_lock.locked():
        return False

    def _run() -> None:
        with _proc_lock:
            db = new_session()
            try:
                pipeline.run_until_idle(db, max_steps=100)
                publishing.tick(db)
            except Exception:
                log.exception("process_in_background")
            finally:
                db.close()

    threading.Thread(target=_run, daemon=True).start()
    return True
