"""Client Twitch Helix reale. NEEDS_LIVE_VERIFICATION: endpoint/campi seguono il documento master,
la documentazione ufficiale non era raggiungibile dal sandbox di sviluppo (vedi docs/DECISIONS.md)."""
from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from app.adapters.twitch.base import ClipMeta, DownloadUrls, TwitchUser
from app.core.errors import PermanentError, TemporaryError
from app.core.timeutil import naive_utc

API = "https://api.twitch.tv/helix"
OAUTH = "https://id.twitch.tv/oauth2"
SCOPES = ["channel:manage:clips"]
MAX_WINDOW_DAYS = 7  # finestre più ampie vengono suddivise (scelta prudenziale)


def _rfc3339(dt: datetime) -> str:
    return dt.replace(tzinfo=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class TokenProvider:
    """Fornisce token app (client credentials) e token utente (con refresh)."""

    def __init__(self, client_id: str, client_secret: str, get_user_tokens, save_user_tokens, http: httpx.Client | None = None):
        self.client_id, self.client_secret = client_id, client_secret
        self._get_user, self._save_user = get_user_tokens, save_user_tokens
        self.http = http or httpx.Client(timeout=20)
        self._app: tuple[str, float] | None = None

    def app_token(self) -> str:
        if self._app and self._app[1] > time.time() + 60:
            return self._app[0]
        r = self.http.post(f"{OAUTH}/token", data={"client_id": self.client_id, "client_secret": self.client_secret, "grant_type": "client_credentials"})
        if r.status_code != 200:
            raise PermanentError(f"Twitch app token fallito ({r.status_code})", code="twitch_auth")
        j = r.json()
        self._app = (j["access_token"], time.time() + int(j.get("expires_in", 3600)))
        return self._app[0]

    def user_token(self) -> str:
        access, refresh = self._get_user()
        if not access:
            raise PermanentError("Twitch non autorizzato: completa il collegamento OAuth da Settings", code="twitch_not_authorized")
        return access

    def refresh_user(self) -> str:
        _, refresh = self._get_user()
        if not refresh:
            raise PermanentError("Refresh token Twitch assente: riconnetti l'account", code="twitch_not_authorized")
        r = self.http.post(f"{OAUTH}/token", data={"client_id": self.client_id, "client_secret": self.client_secret,
                                                    "grant_type": "refresh_token", "refresh_token": refresh})
        if r.status_code != 200:
            raise PermanentError("Rinnovo token Twitch fallito: riconnetti l'account", code="twitch_not_authorized")
        j = r.json()
        self._save_user(j["access_token"], j.get("refresh_token", refresh))
        return j["access_token"]


class HelixClient:
    simulated = False

    def __init__(self, tokens: TokenProvider, client_id: str, http: httpx.Client | None = None):
        self.tokens, self.client_id = tokens, client_id
        self.http = http or httpx.Client(timeout=30)

    def _get(self, path: str, params, *, user: bool = False) -> dict:
        for attempt in range(2):
            token = self.tokens.user_token() if user else self.tokens.app_token()
            try:
                r = self.http.get(f"{API}{path}", params=params, headers={"Authorization": f"Bearer {token}", "Client-Id": self.client_id})
            except httpx.HTTPError as exc:
                raise TemporaryError(f"Twitch non raggiungibile: {exc}", code="twitch_unreachable") from exc
            if r.status_code == 401 and attempt == 0:
                if user:
                    self.tokens.refresh_user()
                else:
                    self.tokens._app = None
                continue
            if r.status_code == 429:
                raise TemporaryError("Rate limit Twitch", code="twitch_rate_limited")
            if r.status_code >= 500:
                raise TemporaryError(f"Twitch {r.status_code}", code="twitch_5xx")
            if r.status_code >= 400:
                raise PermanentError(f"Twitch {r.status_code}: {r.text[:200]}", code="twitch_error")
            return r.json()
        raise PermanentError("Autenticazione Twitch fallita", code="twitch_auth")

    def resolve_user(self, login: str) -> TwitchUser:
        d = self._get("/users", {"login": login.lstrip("@")})["data"]
        if not d:
            raise PermanentError(f"Utente Twitch '{login}' non trovato", code="twitch_user_not_found")
        return TwitchUser(d[0]["id"], d[0]["login"], d[0]["display_name"])

    @staticmethod
    def _meta(c: dict) -> ClipMeta:
        return ClipMeta(
            id=c["id"], broadcaster_id=c["broadcaster_id"], broadcaster_name=c.get("broadcaster_name", ""),
            creator_id=c.get("creator_id", ""), creator_name=c.get("creator_name", ""), game_id=c.get("game_id", ""),
            game_name="", title=c.get("title", ""), url=c.get("url", ""),
            created_at=naive_utc(datetime.fromisoformat(c["created_at"].replace("Z", "+00:00"))),
            duration=float(c.get("duration", 0)), view_count=int(c.get("view_count", 0)), thumbnail_url=c.get("thumbnail_url", ""),
        )

    def _game_names(self, ids: set[str]) -> dict[str, str]:
        out: dict[str, str] = {}
        ids = {i for i in ids if i}
        lst = list(ids)
        for i in range(0, len(lst), 100):
            for g in self._get("/games", [("id", x) for x in lst[i:i + 100]])["data"]:
                out[g["id"]] = g["name"]
        return out

    def list_clips(self, broadcaster_id: str, started_at: datetime, ended_at: datetime) -> list[ClipMeta]:
        """Pagina completamente (ordinamento per views, non per data) e suddivide finestre > 7 giorni."""
        found: dict[str, ClipMeta] = {}
        cur = started_at
        while cur < ended_at:
            end = min(cur + timedelta(days=MAX_WINDOW_DAYS), ended_at)
            cursor = None
            while True:
                params = {"broadcaster_id": broadcaster_id, "started_at": _rfc3339(cur), "ended_at": _rfc3339(end), "first": 100}
                if cursor:
                    params["after"] = cursor
                j = self._get("/clips", params)
                for c in j.get("data", []):
                    m = self._meta(c)
                    if started_at <= m.created_at <= ended_at:  # filtro per created_at
                        found[m.id] = m
                cursor = j.get("pagination", {}).get("cursor")
                if not cursor:
                    break
            cur = end
        names = self._game_names({m.game_id for m in found.values()})
        for m in found.values():
            m.game_name = names.get(m.game_id, "")
        return list(found.values())

    def get_clip(self, clip_id: str) -> ClipMeta | None:
        d = self._get("/clips", {"id": clip_id})["data"]
        if not d:
            return None
        m = self._meta(d[0])
        m.game_name = self._game_names({m.game_id}).get(m.game_id, "")
        return m

    def get_download_urls(self, broadcaster_id: str, clip_ids: list[str]) -> dict[str, DownloadUrls]:
        out: dict[str, DownloadUrls] = {}
        for i in range(0, len(clip_ids), 10):  # max 10 ID per richiesta
            chunk = clip_ids[i:i + 10]
            params = [("broadcaster_id", broadcaster_id), ("editor_id", broadcaster_id)] + [("clip_id", c) for c in chunk]
            j = self._get("/clips/downloads", params, user=True)
            for d in j.get("data", []):
                out[d["clip_id"]] = DownloadUrls(d["clip_id"], d.get("portrait_download_url") or None, d.get("landscape_download_url") or None)
        return out


class HttpDownloader:
    def __init__(self, http: httpx.Client | None = None):
        self.http = http or httpx.Client(timeout=httpx.Timeout(60, connect=15), follow_redirects=True)

    def download(self, url: str, dest: Path, max_bytes: int) -> int:
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_suffix(dest.suffix + ".part")
        total = 0
        try:
            with self.http.stream("GET", url) as r:
                if r.status_code in (401, 403, 404, 410):
                    raise TemporaryError(f"URL download scaduto/non valido ({r.status_code})", code="download_url_expired")
                if r.status_code >= 400:
                    raise TemporaryError(f"Download HTTP {r.status_code}", code="download_http")
                ctype = r.headers.get("content-type", "")
                if ctype and not (ctype.startswith("video/") or "octet-stream" in ctype):
                    raise PermanentError(f"Content-Type inatteso: {ctype}", code="download_bad_mime")
                with part.open("wb") as f:
                    for chunk in r.iter_bytes(1 << 20):
                        total += len(chunk)
                        if total > max_bytes:
                            raise PermanentError("File oltre il limite di dimensione", code="download_too_large")
                        f.write(chunk)
            part.replace(dest)
        except httpx.HTTPError as exc:
            raise TemporaryError(f"Download interrotto: {exc}", code="download_network") from exc
        finally:
            part.unlink(missing_ok=True)
        return total
