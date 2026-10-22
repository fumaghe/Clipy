"""Client Twitch Helix reale. NEEDS_LIVE_VERIFICATION: endpoint/campi seguono il documento master,
la documentazione ufficiale non era raggiungibile dal sandbox di sviluppo (vedi docs/DECISIONS.md).

Coerenza identità: `broadcaster_id` è il canale (risolto da `login` con /users), `editor_id` è l'utente a cui appartiene
il token OAuth (da /oauth2/validate): coincidono se autorizza il broadcaster, altrimenti l'utente deve essere editor del canale."""
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
REQUIRED_SCOPE = "channel:manage:clips"
CLIP_LIST_CAP = 1000  # una finestra che restituisce questo numero di clip può essere stata troncata → viene bisecata
MIN_SPLIT = timedelta(minutes=15)
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
        self._identity: dict | None = None

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
        try:
            r = self.http.post(f"{OAUTH}/token", data={"client_id": self.client_id, "client_secret": self.client_secret,
                                                        "grant_type": "refresh_token", "refresh_token": refresh})
        except httpx.HTTPError as exc:
            raise TemporaryError(f"Rinnovo token Twitch non riuscito (rete): {type(exc).__name__}", code="twitch_unreachable") from exc
        if r.status_code >= 500:
            raise TemporaryError(f"Twitch {r.status_code} durante il rinnovo del token", code="twitch_5xx")
        if r.status_code != 200:
            raise PermanentError("Rinnovo token Twitch fallito (refresh token revocato o scaduto): riconnetti l'account", code="twitch_not_authorized")
        try:
            j = r.json()
            access = j["access_token"]
        except (ValueError, KeyError) as exc:
            raise TemporaryError("Risposta di rinnovo token Twitch non valida", code="twitch_bad_response") from exc
        self._save_user(access, j.get("refresh_token", refresh))  # Twitch può ruotare il refresh token
        self._identity = None
        return access

    def identity(self) -> dict:
        """Utente proprietario del token e scope concessi (GET /oauth2/validate). Rinnova il token se scaduto."""
        if self._identity:
            return self._identity
        for attempt in range(2):
            token = self.user_token()
            try:
                r = self.http.get(f"{OAUTH}/validate", headers={"Authorization": f"OAuth {token}"})
            except httpx.HTTPError as exc:
                raise TemporaryError(f"Validazione token Twitch non riuscita (rete): {type(exc).__name__}", code="twitch_unreachable") from exc
            if r.status_code == 401 and attempt == 0:
                self.refresh_user()
                continue
            if r.status_code != 200:
                raise PermanentError("Token Twitch non valido: riconnetti l'account", code="twitch_not_authorized")
            j = r.json()
            self._identity = {"user_id": str(j.get("user_id", "")), "login": j.get("login", ""), "scopes": list(j.get("scopes") or [])}
            return self._identity
        raise PermanentError("Token Twitch non valido: riconnetti l'account", code="twitch_not_authorized")


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
                raise TemporaryError(f"Twitch non raggiungibile: {type(exc).__name__}", code="twitch_unreachable") from exc
            if r.status_code == 401 and attempt == 0:
                if user:
                    self.tokens.refresh_user()  # token scaduto: rinnovo e riprovo una volta
                else:
                    self.tokens._app = None
                continue
            if r.status_code == 401:
                raise PermanentError("Twitch rifiuta il token anche dopo il rinnovo: riconnetti l'account", code="twitch_not_authorized")
            if r.status_code == 403:
                raise PermanentError("Twitch 403: scope mancante o l'utente autorizzato non è editor/broadcaster del canale", code="twitch_forbidden")
            if r.status_code == 429:
                reset = r.headers.get("ratelimit-reset", "")
                wait = max(0.0, float(reset) - time.time()) if reset.replace(".", "", 1).isdigit() else None
                raise TemporaryError("Rate limit Twitch", code="twitch_rate_limited", retry_after=wait)
            if r.status_code >= 500:
                raise TemporaryError(f"Twitch {r.status_code}", code="twitch_5xx")
            if r.status_code >= 400:
                raise PermanentError(f"Twitch {r.status_code}: {r.text[:200]}", code="twitch_error")
            try:
                j = r.json()
            except ValueError as exc:
                raise TemporaryError("Risposta Twitch non è JSON valido", code="twitch_bad_response") from exc
            if not isinstance(j, dict):
                raise TemporaryError("Risposta Twitch inattesa", code="twitch_bad_response")
            return j
        raise PermanentError("Autenticazione Twitch fallita", code="twitch_not_authorized")

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

    def _clips_window(self, broadcaster_id: str, start: datetime, end: datetime, found: dict[str, ClipMeta], stats: dict) -> None:
        """Pagina completamente una finestra; se potrebbe essere stata troncata dal tetto dell'API la biseca."""
        cursor, n = None, 0
        while True:
            params = {"broadcaster_id": broadcaster_id, "started_at": _rfc3339(start), "ended_at": _rfc3339(end), "first": 100}
            if cursor:
                params["after"] = cursor
            j = self._get("/clips", params)
            for c in j.get("data") or []:
                n += 1
                try:
                    m = self._meta(c)
                except (KeyError, ValueError, TypeError):
                    stats["malformed"] += 1  # una clip malformata non deve bloccare le altre
                    continue
                found[m.id] = m
            cursor = (j.get("pagination") or {}).get("cursor")
            if not cursor:
                break
        if n >= CLIP_LIST_CAP and end - start > MIN_SPLIT:
            mid = start + (end - start) / 2
            self._clips_window(broadcaster_id, start, mid, found, stats)
            self._clips_window(broadcaster_id, mid, end, found, stats)

    def list_clips(self, broadcaster_id: str, started_at: datetime, ended_at: datetime) -> list[ClipMeta]:
        """Pagina completamente (ordinamento Twitch per views, non per data), suddivide finestre > 7 giorni o troppo piene,
        filtra per created_at, deduplica per ID e restituisce in ordine cronologico. Nessun filtro sul creator."""
        found: dict[str, ClipMeta] = {}
        stats = {"malformed": 0}
        cur = started_at
        while cur < ended_at:
            end = min(cur + timedelta(days=MAX_WINDOW_DAYS), ended_at)
            self._clips_window(broadcaster_id, cur, end, found, stats)
            cur = end
        self.last_stats = stats
        inside = [m for m in found.values() if started_at <= m.created_at <= ended_at]
        names = self._game_names({m.game_id for m in inside})
        for m in inside:
            m.game_name = names.get(m.game_id, "")
        return sorted(inside, key=lambda m: (m.created_at, m.id))

    def get_clip(self, clip_id: str) -> ClipMeta | None:
        d = self._get("/clips", {"id": clip_id})["data"]
        if not d:
            return None
        m = self._meta(d[0])
        m.game_name = self._game_names({m.game_id}).get(m.game_id, "")
        return m

    def get_download_urls(self, broadcaster_id: str, clip_ids: list[str]) -> dict[str, DownloadUrls]:
        out: dict[str, DownloadUrls] = {}
        ident = self.tokens.identity()
        if REQUIRED_SCOPE not in ident["scopes"]:
            raise PermanentError(f"Il token Twitch non ha lo scope {REQUIRED_SCOPE}: ricollega l'account", code="twitch_scope_missing")
        editor_id = ident["user_id"]  # l'utente autenticato (== broadcaster se autorizza il proprietario del canale)
        for i in range(0, len(clip_ids), 10):  # max 10 ID per richiesta
            chunk = clip_ids[i:i + 10]
            params = [("broadcaster_id", broadcaster_id), ("editor_id", editor_id)] + [("clip_id", c) for c in chunk]
            j = self._get("/clips/downloads", params, user=True)
            for d in j.get("data", []):
                out[d["clip_id"]] = DownloadUrls(d["clip_id"], d.get("portrait_download_url") or None, d.get("landscape_download_url") or None)
        return out


class HttpDownloader:
    """Scarica su `.part` e rinomina solo a download completo (dimensione == Content-Length, se dichiarata)."""

    def __init__(self, http: httpx.Client | None = None):
        self.http = http or httpx.Client(timeout=httpx.Timeout(60, connect=15), follow_redirects=True)

    def download(self, url: str, dest: Path, max_bytes: int) -> int:
        if not url.startswith("https://"):
            raise PermanentError("URL di download non HTTPS rifiutata", code="download_bad_url")
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_suffix(dest.suffix + ".part")
        total = 0
        try:
            with self.http.stream("GET", url) as r:
                if r.status_code in (401, 403, 404, 410):
                    raise TemporaryError(f"URL download scaduto/non valido ({r.status_code}): ne richiedo una nuova", code="download_url_expired")
                if r.status_code >= 400:
                    raise TemporaryError(f"Download HTTP {r.status_code}", code="download_http")
                ctype = r.headers.get("content-type", "")
                if ctype and not (ctype.startswith("video/") or "octet-stream" in ctype):
                    raise PermanentError(f"Content-Type inatteso: {ctype}", code="download_bad_mime")
                declared = r.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > max_bytes:
                    raise PermanentError("File oltre il limite di dimensione", code="download_too_large")
                with part.open("wb") as f:
                    for chunk in r.iter_bytes(1 << 20):
                        total += len(chunk)
                        if total > max_bytes:
                            raise PermanentError("File oltre il limite di dimensione", code="download_too_large")
                        f.write(chunk)
                if declared and declared.isdigit() and total != int(declared):
                    raise TemporaryError(f"Download incompleto ({total}/{declared} byte)", code="download_incomplete")
            if total == 0:
                raise TemporaryError("Download vuoto", code="download_incomplete")
            part.replace(dest)
        except httpx.HTTPError as exc:
            raise TemporaryError(f"Download interrotto: {type(exc).__name__}", code="download_network") from exc
        finally:
            part.unlink(missing_ok=True)
        return total
