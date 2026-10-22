"""Costruzione degli adapter a partire da impostazioni e segreti. `OVERRIDES` serve ai test."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.adapters.ai.analyzers import LLMAnalyzer, MockAnalyzer
from app.adapters.ai.transcribe import FasterWhisperTranscriber, MockTranscriber
from app.adapters.publisher.buffer import BufferPublisher
from app.adapters.publisher.mock import MockPublisher
from app.adapters.storage.local import MockStorage
from app.adapters.storage.s3 import S3Storage
from app.adapters.twitch.helix import HelixClient, HttpDownloader, TokenProvider
from app.adapters.twitch.mock import FixtureDownloader, MockTwitch
from app.core.errors import PermanentError
from app.core.settings_model import ProductSettings
from app.services import settings_service as ss

OVERRIDES: dict[str, Any] = {}


def clip_source(db: Session, settings: ProductSettings):
    if "clip_source" in OVERRIDES:
        return OVERRIDES["clip_source"]
    cid, sec = ss.get_secret(db, "twitch_client_id"), ss.get_secret(db, "twitch_client_secret")
    if settings.system.demo_mode or not cid:
        return MockTwitch()
    tokens = TokenProvider(cid, sec, lambda: (ss.get_secret(db, "twitch_access_token"), ss.get_secret(db, "twitch_refresh_token")),
                           lambda a, r: (ss.set_secret(db, "twitch_access_token", a), ss.set_secret(db, "twitch_refresh_token", r), db.commit()))
    return HelixClient(tokens, cid)


def downloader(db: Session, settings: ProductSettings, source):
    if "downloader" in OVERRIDES:
        return OVERRIDES["downloader"]
    return FixtureDownloader() if getattr(source, "simulated", False) else HttpDownloader()


def transcriber(db: Session, settings: ProductSettings, demo_clip: bool):
    if "transcriber" in OVERRIDES:
        return OVERRIDES["transcriber"]
    if demo_clip:
        return MockTranscriber()
    return FasterWhisperTranscriber(settings.ai.whisper_model, settings.ai.whisper_device, settings.ai.asr_timeout_seconds)


def analyzer(db: Session, settings: ProductSettings, demo_clip: bool):
    if "analyzer" in OVERRIDES:
        return OVERRIDES["analyzer"]
    a = settings.ai
    if demo_clip or a.provider == "mock":
        return MockAnalyzer()
    return LLMAnalyzer(a.provider, ss.get_secret(db, "llm_api_key"), a.model, a.base_url, a.vision_enabled, timeout=a.llm_timeout_seconds)


def storage(db: Session, settings: ProductSettings, *, for_live: bool):
    if "storage" in OVERRIDES:
        return OVERRIDES["storage"]
    st = settings.storage
    if st.provider == "s3":
        return S3Storage(bucket=st.bucket, endpoint_url=st.endpoint_url, region=st.region,
                         access_key=ss.get_secret(db, "s3_access_key_id"), secret_key=ss.get_secret(db, "s3_secret_access_key"),
                         public_base_url=st.public_base_url, key_prefix=st.key_prefix)
    if for_live:
        raise PermanentError("Storage pubblico non configurato: Buffer richiede un URL HTTPS pubblico (R2/S3)", code="storage_not_configured")
    return MockStorage()


def publisher(db: Session, settings: ProductSettings):
    if "publisher" in OVERRIDES:
        return OVERRIDES["publisher"]
    if settings.publishing.provider == "mock":
        return MockPublisher()
    return BufferPublisher(ss.get_secret(db, "buffer_api_key"), organization_id=settings.publishing.organization_id)
