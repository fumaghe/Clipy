"""MockPublisher: simula il ciclo di vita remoto (scheduled → sent a due_at). Stato in memoria/file, SIMULATO."""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime

from app.adapters.publisher.base import PublishRequest, RemotePost
from app.core.config import get_env
from app.core.timeutil import utcnow

_lock = threading.Lock()


class MockPublisher:
    name = "mock"
    simulated = True

    def __init__(self, fail_next: str | None = None):
        self.path = get_env().dir("fixtures") / "mock_publisher.json"
        self.fail_next = fail_next

    def _load(self) -> dict:
        with _lock:
            return json.loads(self.path.read_text("utf-8")) if self.path.exists() else {"posts": {}}

    def _save(self, d: dict) -> None:
        with _lock:
            self.path.write_text(json.dumps(d), "utf-8")

    def test_connection(self) -> dict:
        return {"ok": True, "simulated": True, "channels": [{"id": "mock-tiktok", "name": "TikTok (SIMULATO)", "service": "tiktok"}]}

    def create_post(self, req: PublishRequest) -> RemotePost:
        d = self._load()
        for p in d["posts"].values():  # idempotenza su fingerprint
            if p["fingerprint"] == req.fingerprint:
                return self._rp(p)
        pid = "mock-" + uuid.uuid4().hex[:12]
        d["posts"][pid] = {"id": pid, "fingerprint": req.fingerprint, "channel": req.channel_id, "text": req.text,
                           "due_at": req.due_at.isoformat(), "video_url": req.video_url}
        self._save(d)
        return self._rp(d["posts"][pid])

    def _rp(self, p: dict) -> RemotePost:
        due = datetime.fromisoformat(p["due_at"])
        status = "sent" if due <= utcnow() else "scheduled"
        return RemotePost(p["id"], status, due, p["text"], None)

    def get_post(self, remote_id: str) -> RemotePost | None:
        p = self._load()["posts"].get(remote_id)
        return self._rp(p) if p else None

    def find_posts(self, channel_id: str, due_at: datetime) -> list[RemotePost]:
        return [self._rp(p) for p in self._load()["posts"].values() if p["channel"] == channel_id and p["due_at"][:16] == due_at.isoformat()[:16]]

    def count_scheduled(self, channel_id: str) -> int:
        return sum(1 for p in self._load()["posts"].values() if p["channel"] == channel_id and self._rp(p).status == "scheduled")
