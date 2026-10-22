from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass
class StoredAsset:
    key: str
    url: str
    public: bool  # True solo se raggiungibile senza autenticazione da internet


class MediaStorage(Protocol):
    name: str

    def upload(self, path: Path, key: str) -> StoredAsset: ...
    def verify_public(self, url: str) -> tuple[bool, str]: ...
    def delete(self, key: str) -> None: ...
