"""Regression test STEP 2.1 — bug emersi dalla demo su Windows (WinError 10054, preview 404, state machine, 409 sul render)."""
from __future__ import annotations

import logging
import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from app.adapters.twitch.mock import MockTwitch
from app.core.timeutil import utcnow
from app.db.models import Analysis, Clip, Event, PublicationJob, Review, StateTransition
from app.domain import states as S
from app.services import collector, factory, pipeline, queue

from .conftest import UniqueAnalyzer


@pytest.fixture(autouse=True)
def _no_background_worker(monkeypatch):
    """Gli endpoint avviano la pipeline in un thread: nei test lo stato intermedio deve restare osservabile (si avanza a mano)."""
    from app.workers import runner

    monkeypatch.setattr(runner, "process_in_background", lambda: False)


def _ready_clip(db) -> Clip:
    factory.OVERRIDES["analyzer"] = UniqueAnalyzer()
    MockTwitch().spawn("funny_portrait", minutes_ago=100)
    collector.scan(db)
    pipeline.run_until_idle(db, 60)
    c = db.scalar(select(Clip).where(Clip.status == S.AUTO_READY))
    assert c is not None and c.render_path
    return c


def _illegal(db) -> list[Event]:
    return list(db.scalars(select(Event).where(Event.type == "illegal_transition")))


# ------------------------------------------------------------------ Problema 3: state machine
def test_reprocess_transitions_are_declared_legal():
    for old in (S.AUTO_READY, S.NEEDS_REVIEW, S.APPROVED, S.REJECTED, S.SKIPPED):
        assert S.is_legal("clip", old, S.DOWNLOADED), old
        assert S.is_legal("clip", old, S.ANALYZED), old
    assert not S.is_legal("clip", S.AUTO_READY, S.DISCOVERED)  # il controllo resta in vigore


def test_reanalysis_of_ready_clip_is_explicit_atomic_and_lossless(client, db):
    c = _ready_clip(db)
    cid, old_render, old_version = c.id, c.render_path, c.content_version
    queue.rebuild(db)
    db.commit()
    first_job = db.scalar(select(PublicationJob).where(PublicationJob.clip_id == cid))
    assert first_job.status == S.QUEUED_LOCAL
    old_title = c.overlay_title
    n_analyses = db.query(Analysis).filter(Analysis.clip_id == cid).count()
    db.close()

    r = client.post(f"/api/v1/clips/{cid}/analyze")
    assert r.status_code == 200 and r.json()["status"] == S.DOWNLOADED

    db.expire_all()
    c = db.get(Clip, cid)
    assert not _illegal(db), [e.message for e in _illegal(db)]
    assert c.status == S.DOWNLOADED and c.render_path is None and c.content_version == old_version + 1
    assert Path(old_render).exists()  # nessun file cancellato
    assert db.get(PublicationJob, first_job.id).status == S.CANCELLED
    assert db.query(Analysis).filter(Analysis.clip_id == cid).count() == n_analyses  # nessuna analisi persa
    snap = db.scalars(select(Review).where(Review.clip_id == cid, Review.policy_version == "reprocess")).all()
    assert snap and snap[0].changes["previous"]["overlay_title"] == old_title  # testo precedente conservato
    note = db.scalar(select(StateTransition).where(StateTransition.entity_id == cid).order_by(StateTransition.id.desc()))
    assert (note.from_state, note.to_state, note.note) == (S.AUTO_READY, S.DOWNLOADED, "reprocess:analyze")

    # richiesta ripetuta mentre è già in coda: no-op (nessuna transizione/versione in più)
    again = client.post(f"/api/v1/clips/{cid}/analyze")
    assert again.status_code == 200
    db.expire_all()
    assert db.get(Clip, cid).content_version == old_version + 1

    # la pipeline completa il giro e la coda crea UN solo job nuovo per la nuova versione (nessun duplicato)
    pipeline.run_until_idle(db, 60)
    queue.rebuild(db)
    db.commit()
    db.expire_all()
    c = db.get(Clip, cid)
    assert c.status in (S.AUTO_READY, S.NEEDS_REVIEW) and c.render_path and Path(c.render_path) != Path(old_render)
    live = [j for j in db.scalars(select(PublicationJob).where(PublicationJob.clip_id == cid)) if j.status in S.PUB_OPEN]
    assert len(live) == (1 if c.status == S.AUTO_READY else 0)
    assert not _illegal(db)


@pytest.mark.parametrize("final", [S.REJECTED, S.SKIPPED])
def test_reanalysis_works_from_rejected_and_skipped(client, db, final):
    c = _ready_clip(db)
    cid = c.id
    c.status = final
    db.commit()
    assert client.post(f"/api/v1/clips/{cid}/analyze").json()["status"] == S.DOWNLOADED
    db.expire_all()
    assert not _illegal(db)


def test_reprocess_refused_when_already_sent_to_provider(client, db):
    """Un contenuto già pianificato/pubblicato non si rielabora: il nuovo content_version produrrebbe un secondo post."""
    c = _ready_clip(db)
    cid, version = c.id, c.content_version
    queue.rebuild(db)
    db.commit()
    job = db.scalar(select(PublicationJob).where(PublicationJob.clip_id == cid))
    job.status = S.SCHEDULED_REMOTE
    db.commit()
    for path in ("analyze", "render"):
        r = client.post(f"/api/v1/clips/{cid}/{path}")
        assert r.status_code == 409 and r.json()["error"]["code"] == "publication_locked", path
    r = client.patch(f"/api/v1/reviews/{cid}/content", json={"caption": "altra caption"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "publication_locked"
    assert client.post(f"/api/v1/reviews/{cid}/regenerate").status_code == 409
    db.expire_all()
    c = db.get(Clip, cid)
    assert c.status == S.AUTO_READY and c.content_version == version and c.render_path
    assert db.query(PublicationJob).filter(PublicationJob.clip_id == cid).count() == 1


def test_worker_ignores_stale_status_after_api_reprocess(db):
    """Il worker sceglie la clip per stato, poi prende il lock: se nel frattempo l'API l'ha spostata, non deve eseguire lo step vecchio."""
    c = _ready_clip(db)
    cid = c.id
    pipeline.reprocess(db, c, stage="analyze")
    real = pipeline.next_clip_id
    try:
        pipeline.next_clip_id = lambda _db: (cid, S.ANALYZED)  # stato "vecchio": avrebbe eseguito step_render
        assert pipeline.advance_one(db) is True
    finally:
        pipeline.next_clip_id = real
    db.expire_all()
    assert db.get(Clip, cid).status == S.DOWNLOADED and db.get(Clip, cid).render_path is None


# ------------------------------------------------------------------ Problema 4: 409 sul render
def test_render_conflict_is_expected_busy_and_exposed_to_the_ui(client, db):
    c = _ready_clip(db)
    cid = c.id
    c.status = S.RENDERING
    db.commit()
    r = client.post(f"/api/v1/clips/{cid}/render")
    assert r.status_code == 409 and r.json()["error"]["code"] == "busy"
    assert "attendi" in r.json()["error"]["message"].lower()
    for path in ("analyze", "skip"):
        assert client.post(f"/api/v1/clips/{cid}/{path}").status_code == 409, path
    assert client.patch(f"/api/v1/reviews/{cid}/content", json={"caption": "x"}).status_code == 409
    dto = client.get(f"/api/v1/clips/{cid}").json()
    assert dto["busy"] is True and dto["busy_reason"] == "Render in corso"
    db.expire_all()
    assert db.get(Clip, cid).status == S.RENDERING  # il 409 non ha toccato nulla


def test_row_lock_held_by_worker_blocks_api_and_is_released_after(client, db):
    c = _ready_clip(db)
    cid = c.id
    c.locked_until = utcnow().replace(microsecond=0) + __import__("datetime").timedelta(minutes=5)
    db.commit()
    assert client.get(f"/api/v1/clips/{cid}").json()["busy"] is True
    assert client.post(f"/api/v1/clips/{cid}/render").status_code == 409
    c.locked_until = None
    db.commit()
    r = client.post(f"/api/v1/clips/{cid}/render")
    assert r.status_code == 200 and r.json()["status"] == S.ANALYZED
    db.expire_all()
    assert db.get(Clip, cid).locked_until is None  # il lock dell'API è sempre rilasciato
    assert client.get(f"/api/v1/clips/{cid}").json()["busy"] is False


def test_concurrent_render_requests_do_not_double_render(client, db):
    """Due click ravvicinati: il primo prepara il render, il secondo è no-op (la clip è già in coda) — mai due versioni."""
    c = _ready_clip(db)
    cid, v = c.id, c.content_version
    assert client.post(f"/api/v1/clips/{cid}/render").status_code == 200
    assert client.post(f"/api/v1/clips/{cid}/render").status_code == 200
    db.expire_all()
    assert db.get(Clip, cid).content_version == v + 1


# ------------------------------------------------------------------ Problema 2: preview 404
def test_preview_of_missing_file_is_not_ready_not_an_error(client, db):
    factory.OVERRIDES["analyzer"] = UniqueAnalyzer()
    MockTwitch().spawn("funny_portrait", minutes_ago=100)
    collector.scan(db)  # DISCOVERED: nessun file ancora
    c = db.scalar(select(Clip))
    r = client.get(f"/api/v1/clips/{c.id}/preview?kind=original")
    assert r.status_code == 404 and r.json()["error"]["code"] == "media_not_ready"
    assert r.headers["cache-control"] == "no-store"
    assert client.get(f"/api/v1/clips/{c.id}/video?kind=render").json()["error"]["code"] == "media_not_ready"
    assert client.get("/api/v1/clips/nonexistent/preview").json()["error"]["code"] == "clip_not_found"
    assert not db.scalars(select(Event).where(Event.severity == "error")).all()


def test_preview_falls_back_to_original_while_render_is_pending(client, db):
    c = _ready_clip(db)
    cid = c.id
    pipeline.reprocess(db, c, stage="render")  # render invalidato, originale presente
    r = client.get(f"/api/v1/clips/{cid}/preview")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    dto = client.get(f"/api/v1/clips/{cid}").json()
    assert dto["has_original"] and not dto["has_render"] and dto["pending"] is True


# ------------------------------------------------------------------ Problema 1: streaming / WinError 10054
def test_video_range_requests(client, db):
    c = _ready_clip(db)
    url = f"/api/v1/clips/{c.id}/video?kind=render"
    size = Path(c.render_path).stat().st_size
    full = client.get(url)
    assert full.status_code == 200 and full.headers["accept-ranges"] == "bytes" and len(full.content) == size
    part = client.get(url, headers={"Range": "bytes=0-1023"})
    assert part.status_code == 206 and part.headers["content-range"] == f"bytes 0-1023/{size}" and part.content == full.content[:1024]
    tail = client.get(url, headers={"Range": f"bytes={size - 100}-"})
    assert tail.status_code == 206 and tail.content == full.content[-100:]
    suffix = client.get(url, headers={"Range": "bytes=-50"})
    assert suffix.status_code == 206 and suffix.content == full.content[-50:]
    bad = client.get(url, headers={"Range": f"bytes={size + 10}-"})
    assert bad.status_code == 416 and bad.headers["content-range"] == f"bytes */{size}"
    head = client.head(url)
    assert head.status_code == 200 and int(head.headers["content-length"]) == size


class _LiveServer:
    def __init__(self):
        import uvicorn

        from app.main import app

        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        self.port = s.getsockname()[1]
        s.close()
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        for _ in range(100):
            if self.server.started:
                return self
            time.sleep(0.05)
        raise RuntimeError("server non avviato")

    def __exit__(self, *a):
        self.server.should_exit = True
        self.thread.join(5)


def test_client_aborting_range_stream_does_not_break_server_or_log_errors(env, db):
    """Il browser annulla spesso gli stream (seek, preload, modale chiusa): il server deve restare sano e non loggare errori."""
    c = _ready_clip(db)
    big = Path(c.render_path)
    big.write_bytes(big.read_bytes() * 40)  # abbastanza grande da non stare nei buffer del socket
    records: list[logging.LogRecord] = []

    class Grab(logging.Handler):
        def emit(self, record):
            records.append(record)

    h = Grab(level=logging.WARNING)
    logging.getLogger().addHandler(h)
    try:
        with _LiveServer() as srv:
            for _ in range(5):
                s = socket.create_connection(("127.0.0.1", srv.port))
                s.sendall(f"GET /api/v1/clips/{c.id}/video?kind=render HTTP/1.1\r\nHost: 127.0.0.1\r\nRange: bytes=0-\r\n\r\n".encode())
                s.recv(4096)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, b"\x01\x00\x00\x00\x00\x00\x00\x00")  # chiusura con RST, come un browser che annulla
                s.close()
            time.sleep(0.5)
            ok = httpx.get(f"http://127.0.0.1:{srv.port}/api/v1/clips/{c.id}/video?kind=render", headers={"Range": "bytes=0-99"})
            assert ok.status_code == 206 and len(ok.content) == 100
    finally:
        logging.getLogger().removeHandler(h)
    assert not [r for r in records if r.levelno >= logging.ERROR], [r.getMessage() for r in records]


def _record(exc: BaseException, msg: str) -> logging.LogRecord:
    return logging.LogRecord("asyncio", logging.ERROR, __file__, 1, msg, (), (type(exc), exc, None))


def test_client_disconnect_filter_only_downgrades_benign_transport_resets():
    from app.main import ClientDisconnectFilter

    f = ClientDisconnectFilter()
    reset = ConnectionResetError(10054, "Una connessione esistente è stata chiusa forzatamente dall'host remoto")
    benign = _record(reset, "Exception in callback _ProactorBasePipeTransport._call_connection_lost(None)")
    assert f.filter(benign) is False and benign.levelno == logging.DEBUG  # asyncio logger a livello INFO: scartato
    # altre eccezioni o altri messaggi NON vengono nascosti
    for rec in (_record(ValueError("boom"), "Exception in callback _ProactorBasePipeTransport._call_connection_lost(None)"),
                _record(RuntimeError("x"), "Task exception was never retrieved"),
                _record(reset, "Task exception was never retrieved")):
        assert f.filter(rec) is True and rec.levelno == logging.ERROR


def test_retention_survives_file_still_open_on_windows(db, monkeypatch):
    from app.services import retention

    c = _ready_clip(db)
    c.status = S.REJECTED
    c.updated_at = utcnow().replace(year=2020)
    db.commit()
    real = Path.unlink

    def locked(self, *a, **k):  # WinError 32/5: file aperto da uno streaming
        if self.suffix == ".mp4":
            raise PermissionError(13, "file in uso")
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", locked)
    retention.cleanup(db)  # non deve sollevare
    db.commit()
    db.refresh(c)
    assert c.original_path and c.render_path  # riferimenti conservati: riproverà al prossimo ciclo
    assert db.scalars(select(Event).where(Event.type == "file_in_use")).first() is not None
