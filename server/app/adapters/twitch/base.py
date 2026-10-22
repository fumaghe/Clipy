from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol


@dataclass
class TwitchUser:
    id: str
    login: str
    display_name: str


@dataclass
class ClipMeta:
    id: str
    broadcaster_id: str
    broadcaster_name: str
    creator_id: str
    creator_name: str
    game_id: str
    game_name: str
    title: str
    url: str
    created_at: datetime  # UTC naive
    duration: float
    view_count: int = 0
    thumbnail_url: str = ""
    extra: dict = field(default_factory=dict)  # demo: scenario, fixture key


@dataclass
class DownloadUrls:
    clip_id: str
    portrait: str | None
    landscape: str | None


class ClipSource(Protocol):
    simulated: bool

    def resolve_user(self, login: str) -> TwitchUser: ...
    def list_clips(self, broadcaster_id: str, started_at: datetime, ended_at: datetime) -> list[ClipMeta]: ...
    def get_clip(self, clip_id: str) -> ClipMeta | None: ...
    def get_download_urls(self, broadcaster_id: str, clip_ids: list[str]) -> dict[str, DownloadUrls]: ...


class Downloader(Protocol):
    def download(self, url: str, dest: Path, max_bytes: int) -> int: ...
