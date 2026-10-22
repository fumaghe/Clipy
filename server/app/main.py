from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import ai, clips, queue, reviews, settings, system, templates
from app.core.config import get_env
from app.core.errors import ClipyError, TemporaryError
from app.db.base import run_migrations, session_scope
from app.services import settings_service as ss
from app.services.templates import seed_templates
from app.workers import runner

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("alembic").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)


def bootstrap() -> None:
    env = get_env()
    env.data_dir.mkdir(parents=True, exist_ok=True)
    run_migrations()
    with session_scope() as db:
        ss.get_settings(db)
        seed_templates(db)
        _recover(db)


def _recover(db) -> None:
    """Dopo un riavvio: rilascia i lock orfani (i job riprendono dal loro stato persistito)."""
    from sqlalchemy import update

    from app.db.models import Clip, PublicationJob

    db.execute(update(Clip).values(locked_until=None))
    db.execute(update(PublicationJob).values(locked_until=None))


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

    for r in (system.router, clips.router, reviews.router, queue.router, templates.router, settings.router, ai.router):
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
