"""MockStorage / LocalStorage: copia in data/public. URL locale NON pubblico (solo demo/dry-run)."""
from __future__ import annotations

import shutil
from pathlib import Path

from app.adapters.storage.base import StoredAsset
from app.core.config import get_env


class MockStorage:
    name = "mock-local"

    def __init__(self, base_url: str = "http://127.0.0.1:8000/media/public"):
        self.base_url = base_url.rstrip("/")
        self.root = get_env().dir("public")

    def upload(self, path: Path, key: str) -> StoredAsset:
        dest = self.root / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)
        return StoredAsset(key=key, url=f"{self.base_url}/{key}", public=False)

    def verify_public(self, url: str) -> tuple[bool, str]:
        key = url.rsplit("/", 1)[-1]
        ok = (self.root / key).exists()
        return ok, "file locale presente (URL NON pubblico: solo demo)" if ok else "asset non trovato"

    def delete(self, key: str) -> None:
        (self.root / key).unlink(missing_ok=True)
