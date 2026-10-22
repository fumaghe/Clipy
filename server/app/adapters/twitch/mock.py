"""MockTwitch: clip simulate persistite in data/fixtures/mock_twitch.json. Marcato `simulated=True`."""
from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta
from pathlib import Path

from app.adapters.twitch.base import ClipMeta, DownloadUrls, TwitchUser
from app.adapters.video.fixtures import FIXTURES, ensure_fixture
from app.core.config import get_env
from app.core.errors import PermanentError
from app.core.timeutil import utcnow

_lock = threading.Lock()


class MockTwitch:
    simulated = True

    def __init__(self):
        self.path = get_env().dir("fixtures") / "mock_twitch.json"

    # --- persistenza ---
    def _load(self) -> list[dict]:
        with _lock:
            if self.path.exists():
                return json.loads(self.path.read_text("utf-8"))
        return []

    def _save(self, rows: list[dict]) -> None:
        with _lock:
            self.path.write_text(json.dumps(rows, indent=1), "utf-8")

    def spawn(self, fixture_key: str | None = None, *, minutes_ago: float = 1, creator: str | None = None) -> ClipMeta:
        """Simula la creazione di una nuova clip su Twitch (usata dal bottone demo)."""
        rows = self._load()
        keys = list(FIXTURES)
        key = fixture_key or keys[len(rows) % len(keys)]
        fx = FIXTURES[key]
        n = len(rows) + 1
        cid = f"DemoClip{n:04d}-{key}"
        row = {
            "id": cid, "fixture": key, "created_at": (utcnow() - timedelta(minutes=minutes_ago)).isoformat(),
            "creator": creator or fx.creator, "views": 3 * n,
        }
        rows.append(row)
        self._save(rows)
        return self._to_meta(row)

    def _to_meta(self, r: dict) -> ClipMeta:
        fx = FIXTURES[r["fixture"]]
        return ClipMeta(
            id=r["id"], broadcaster_id="demo-broadcaster", broadcaster_name="iDensTv",
            creator_id="u-" + r["creator"], creator_name=r["creator"], game_id="g-" + fx.game, game_name=fx.game,
            title=fx.title, url=f"https://www.twitch.tv/idenstv/clip/{r['id']}",
            created_at=datetime.fromisoformat(r["created_at"]), duration=fx.seconds, view_count=r["views"],
            extra={"fixture": r["fixture"], "scenario": fx.scenario, "portrait": fx.portrait_on_twitch},
        )

    def seed_if_empty(self) -> None:
        if self._load():
            return
        for i, key in enumerate(FIXTURES):
            self.spawn(key, minutes_ago=60 * (i + 1) * 2)
        # una clip vecchia (fuori dalla finestra di bootstrap 24h)
        rows = self._load()
        rows.append({"id": "DemoClipOLD-funny_portrait", "fixture": "funny_portrait",
                     "created_at": (utcnow() - timedelta(days=4)).isoformat(), "creator": "viewer_old", "views": 99})
        self._save(rows)

    # --- ClipSource ---
    def resolve_user(self, login: str) -> TwitchUser:
        return TwitchUser(id="demo-broadcaster", login=login.lower(), display_name=login)

    def list_clips(self, broadcaster_id: str, started_at: datetime, ended_at: datetime) -> list[ClipMeta]:
        self.seed_if_empty()
        out = []
        for r in self._load():
            m = self._to_meta(r)
            if started_at <= m.created_at <= ended_at:
                out.append(m)
        return out

    def get_clip(self, clip_id: str) -> ClipMeta | None:
        self.seed_if_empty()
        for r in self._load():
            if r["id"] == clip_id:
                return self._to_meta(r)
        return None

    def get_download_urls(self, broadcaster_id: str, clip_ids: list[str]) -> dict[str, DownloadUrls]:
        out = {}
        for cid in clip_ids:
            m = self.get_clip(cid)
            if not m:
                continue
            key = m.extra["fixture"]
            ensure_fixture(key)
            url = f"fixture://{key}"
            out[cid] = DownloadUrls(cid, url if m.extra["portrait"] else None, url)
        return out


class FixtureDownloader:
    def download(self, url: str, dest: Path, max_bytes: int) -> int:
        if not url.startswith("fixture://"):
            raise PermanentError("FixtureDownloader supporta solo fixture://", code="bad_url")
        src = ensure_fixture(url[len("fixture://"):])
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(src.read_bytes())
        return dest.stat().st_size
