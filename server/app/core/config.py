"""Configurazione di processo (env). Le impostazioni di prodotto vivono nel DB (vedi settings_model)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class EnvConfig(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(REPO_ROOT / ".env"), env_prefix="CLIPY_", extra="ignore")

    host: str = "127.0.0.1"
    port: int = 8000
    data_dir: Path = REPO_ROOT / "data"
    database_url: str | None = None
    run_worker: bool = True
    allowed_hosts: str = "127.0.0.1,localhost,testserver"  # header Host accettati (anti DNS-rebinding); "testserver" = TestClient
    cors_origins: str = "http://127.0.0.1:5173,http://localhost:5173"
    templates_dir: Path = REPO_ROOT / "templates"
    frontend_dist: Path = REPO_ROOT / "frontend" / "dist"
    # test hook: tick più rapidi
    pipeline_tick_seconds: int = 5
    publisher_tick_seconds: int = 20

    twitch_client_id: str = Field(default="", validation_alias="TWITCH_CLIENT_ID")
    twitch_client_secret: str = Field(default="", validation_alias="TWITCH_CLIENT_SECRET")
    twitch_redirect_uri: str = Field(
        default="http://localhost:8000/api/v1/integrations/twitch/callback", validation_alias="TWITCH_REDIRECT_URI"
    )
    buffer_api_key: str = Field(default="", validation_alias="BUFFER_API_KEY")
    llm_api_key: str = Field(default="", validation_alias="LLM_API_KEY")
    s3_access_key_id: str = Field(default="", validation_alias="S3_ACCESS_KEY_ID")
    s3_secret_access_key: str = Field(default="", validation_alias="S3_SECRET_ACCESS_KEY")
    twitch_access_token: str = ""
    twitch_refresh_token: str = ""

    @property
    def db_url(self) -> str:
        return self.database_url or f"sqlite:///{(self.data_dir / 'clipy.db').as_posix()}"

    def dir(self, name: str) -> Path:
        p = self.data_dir / name
        p.mkdir(parents=True, exist_ok=True)
        return p


@lru_cache
def get_env() -> EnvConfig:
    cfg = EnvConfig()
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    for sub in ("originals", "rendered", "previews", "temp", "public", "fixtures"):
        (cfg.data_dir / sub).mkdir(parents=True, exist_ok=True)
    return cfg
