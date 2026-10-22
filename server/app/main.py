from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import ai, clips, diagnostics, queue, reviews, settings, system, templates
from app.core.config import get_env
from app.core.errors import ClipyError, TemporaryError
from app.db.base import run_migrations, session_scope
from app.services import settings_service as ss
from app.services.templates import seed_templates
from app.workers import runner

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def install_log_sanitizer() -> None:
    """Applica SecretFilter a ogni handler (root, uvicorn.*): token e parametri OAuth vengono mascherati prima di essere scritti."""
    from app.services.events import SecretFilter

    f = SecretFilter()
    for lg in (logging.getLogger(), logging.getLogger("uvicorn"), logging.getLogger("uvicorn.access"), logging.getLogger("uvicorn.error")):
        for h in lg.handlers:
            if not any(isinstance(x, SecretFilter) for x in h.filters):
                h.addFilter(f)


install_log_sanitizer()
logging.getLogger("alembic").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)


def setup_file_logging() -> None:
    """Log su file ruotato (data/logs/clipy.log): su Windows la console può sparire, il file no. Gli errori sono già sanificati dagli emit."""
    from logging.handlers import RotatingFileHandler

    root = logging.getLogger()
    path = get_env().dir("logs") / "clipy.log"
    if any(isinstance(h, RotatingFileHandler) and h.baseFilename == str(path) for h in root.handlers):
        return
    h = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(h)
    install_log_sanitizer()


def bootstrap() -> None:
    env = get_env()
    env.data_dir.mkdir(parents=True, exist_ok=True)
    setup_file_logging()
    run_migrations()
    with session_scope() as db:
        ss.get_settings(db)
        seed_templates(db)
        n = ss.protect_all(db)  # Windows: cifra con DPAPI gli eventuali segreti V1 in chiaro (valori invariati)
        if n:
            logging.getLogger("clipy").info("Protezione segreti: %d valori cifrati con DPAPI", n)
        _recover(db)


def _recover(db) -> None:
    """Dopo un riavvio: rilascia i lock orfani (i job riprendono dal loro stato persistito).

    Se un'altra istanza è leader del worker con lease valido NON si toccano i lock: appartengono a lavori in corso.
    In quel caso i lock scaduti si liberano da soli e il nuovo leader li recupera quando prende la leadership."""
    from app.services import leases
    from app.workers.runner import OWNER

    if not leases.foreign_leader_alive(db, OWNER):
        leases.recover_locks(db)
    from app.services import housekeeping

    housekeeping.cleanup_stale_files()


@asynccontextmanager
async def lifespan(app: FastAPI):
    bootstrap()
    if get_env().run_worker:
        runner.start()
    yield
    runner.stop()


def create_app() -> FastAPI:
    env = get_env()
    app = FastAPI(title="Clipy API", version="1.0.0", lifespan=lifespan)
    # Difese per un'API locale senza login: Host consentiti (anti DNS-rebinding) e Origin delle richieste che modificano stato (anti CSRF cross-site)
    hosts = [h.strip() for h in env.allowed_hosts.split(",") if h.strip()]
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)
    origins = {o.strip() for o in env.cors_origins.split(",") if o.strip()}

    @app.middleware("http")
    async def _origin_guard(request: Request, call_next):
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            origin = request.headers.get("origin")
            same = f"{request.url.scheme}://{request.headers.get('host', '')}"
            if origin and origin != same and origin not in origins:
                return JSONResponse(status_code=403, content={"error": {"code": "forbidden_origin", "message": "Origine non consentita per questa operazione"}})
        return await call_next(request)

    app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in env.cors_origins.split(",") if o.strip()],
                       allow_methods=["*"], allow_headers=["*"], allow_credentials=False)

    @app.exception_handler(ClipyError)
    async def _clipy(_: Request, exc: ClipyError):
        return JSONResponse(status_code=exc.status_code, content={"error": {"code": exc.code, "message": exc.message}})

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        msg = "; ".join(f"{'.'.join(str(x) for x in e['loc'][1:])}: {e['msg']}" for e in exc.errors())
        return JSONResponse(status_code=422, content={"error": {"code": "validation_error", "message": msg}})

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        logging.getLogger("clipy").exception("errore non gestito")
        code = "temporary_error" if isinstance(exc, TemporaryError) else "internal_error"
        return JSONResponse(status_code=500, content={"error": {"code": code, "message": f"Errore interno: {exc}"[:300]}})

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "app": "Clipy"}

    for r in (system.router, clips.router, reviews.router, queue.router, templates.router, settings.router, ai.router, diagnostics.router):
        app.include_router(r)

    public = env.dir("public")
    app.mount("/media/public", StaticFiles(directory=str(public)), name="public")

    dist = env.frontend_dist
    if dist.exists():
        app.mount("/assets", StaticFiles(directory=str(dist / "assets")), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith(("api/", "media/")):
                return JSONResponse(status_code=404, content={"error": {"code": "not_found", "message": "Non trovato"}})
            f = (dist / path).resolve()
            if path and dist.resolve() in f.parents and f.is_file():
                return FileResponse(f)
            return FileResponse(dist / "index.html")

    return app


app = create_app()
