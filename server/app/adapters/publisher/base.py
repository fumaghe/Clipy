from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass
class PublishRequest:
    channel_id: str
    text: str
    video_url: str
    due_at: datetime  # UTC naive
    fingerprint: str


@dataclass
class RemotePost:
    id: str
    status: str  # scheduled | sent | error | draft | unknown
    due_at: datetime | None = None
    text: str = ""
    url: str | None = None
    error: str | None = None


class Publisher(Protocol):
    name: str
    simulated: bool

    def test_connection(self) -> dict: ...
    def create_post(self, req: PublishRequest) -> RemotePost: ...
    def get_post(self, remote_id: str) -> RemotePost | None: ...
    def find_posts(self, channel_id: str, due_at: datetime) -> list[RemotePost]: ...
    def count_scheduled(self, channel_id: str) -> int: ...
