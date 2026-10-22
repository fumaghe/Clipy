from __future__ import annotations

import os

import pytest

os.environ.setdefault("CLIPY_RUN_WORKER", "false")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPY_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CLIPY_RUN_WORKER", "false")
    from app.core import config
    from app.db import base
    from app.services import factory

    config.get_env.cache_clear()
    base.reset_engine()
    factory.OVERRIDES.clear()
    from app.main import bootstrap

    bootstrap()
    from app.db.base import session_scope
    from app.services import settings_service as ss

    with session_scope() as d:
        ss.update_settings(d, {"render": {"preset": "ultrafast"}})
    yield config.get_env()
    factory.OVERRIDES.clear()
    base.reset_engine()
    config.get_env.cache_clear()


@pytest.fixture()
def db(env):
    from app.db.base import new_session

    s = new_session()
    yield s
    s.close()


@pytest.fixture()
def client(env):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


class UniqueAnalyzer:
    """Analizzatore di test: titoli distinti e coerenti con le evidenze (parola 'carrello' nella trascrizione)."""

    simulated = False
    WORDS = ["volo", "decollo", "salto", "rampa", "schianto", "atterraggio", "giro", "sorpasso", "derapata", "caduta", "acrobazia", "turbo"]

    def __init__(self):
        self.n = 0

    def analyze(self, ctx):
        from app.adapters.ai.base import AnalysisOutput, AnalysisResult

        w = self.WORDS[self.n % len(self.WORDS)] + ("" if self.n < len(self.WORDS) else "x")
        self.n += 1
        out = AnalysisOutput(scene_summary="Scena", overlay_title=f"Il carrello e il {w}", tiktok_caption=f"Che {w} incredibile col carrello",
                             hashtags=["#gaming"], confidence=0.9)
        return AnalysisResult(out, "test", "test-analyzer", "t1", simulated=False)


def set_settings(patch: dict):
    from app.db.base import session_scope
    from app.services import settings_service as ss

    with session_scope() as db:
        ss.update_settings(db, patch)


def force_publishing(**kw):
    """Scrive direttamente i flag protetti (live_enabled/is_connected) come farebbero i flussi dedicati."""
    from app.db.base import session_scope
    from app.db.models import Setting

    with session_scope() as db:
        row = db.get(Setting, "product")
        row.value = {**row.value, "publishing": {**row.value["publishing"], **kw}}
