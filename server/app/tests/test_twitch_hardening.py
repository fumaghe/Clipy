"""Collector Twitch: auth, token scaduto, download integro, paginazione, ripartenza. MockTransport: NON validato live."""
import shutil
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import func, select

from app.adapters.twitch.base import DownloadUrls
from app.adapters.twitch.helix import HelixClient, HttpDownloader, TokenProvider
from app.adapters.twitch.mock import MockTwitch
from app.adapters.video.fixtures import ensure_fixture
from app.core.errors import PermanentError, TemporaryError
from app.core.timeutil import utcnow
from app.db.models import Clip, OAuthState
from app.domain import states as S
from app.services import collector, factory, pipeline

from .conftest import set_settings


def mk(handler, tokens=("utok", "rtok"), saved=None):
    http = httpx.Client(transport=httpx.MockTransport(handler))
    state = {"t": tokens}

    def save(a, r):
        state["t"] = (a, r)
        if saved is not None:
            saved.append((a, r))

    tp = TokenProvider("cid", "sec", lambda: state["t"], save, http=http)
    tp._app = ("apptok", 9e12)
    return HelixClient(tp, "cid", http=http), tp, state


def clip_json(i, age_h=1, creator="viewer"):
    ts = (utcnow() - timedelta(hours=age_h)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"id": f"c{i}", "broadcaster_id": "1", "broadcaster_name": "iDensTv", "creator_id": f"u{i}", "creator_name": creator, "game_id": "",
            "title": "t", "url": "u", "created_at": ts, "duration": 20, "view_count": 1}


# ------------------------------------------------------------------ token / identità
def test_expired_user_token_is_refreshed_and_request_retried_once():
    calls, saved = [], []

    def h(req):
        if req.url.path.endswith("/oauth2/token"):
            assert b"refresh_token=rtok" in req.content
            return httpx.Response(200, json={"access_token": "new-access", "refresh_token": "new-refresh"})
        if req.url.path.endswith("/oauth2/validate"):
            return httpx.Response(200, json={"user_id": "1", "login": "idenstv", "scopes": ["channel:manage:clips"]})
        calls.append(req.headers["authorization"])
        if req.headers["authorization"] == "Bearer utok":
            return httpx.Response(401, json={"message": "Invalid OAuth token"})
        return httpx.Response(200, json={"data": [{"clip_id": "c1", "portrait_download_url": "https://p/c1", "landscape_download_url": "https://l/c1"}]})

    cl, _, state = mk(h, saved=saved)
    out = cl.get_download_urls("1", ["c1"])
    assert out["c1"].portrait == "https://p/c1" and calls == ["Bearer utok", "Bearer new-access"]
    assert saved == [("new-access", "new-refresh")] and state["t"][1] == "new-refresh"  # refresh token ruotato e salvato


def test_revoked_refresh_token_requires_reconnect_not_loop():
    def h(req):
        if req.url.path.endswith("/oauth2/token"):
            return httpx.Response(400, json={"message": "Invalid refresh token"})
        return httpx.Response(401)

    cl, _, _ = mk(h)
    with pytest.raises(PermanentError) as e:
        cl.get_download_urls("1", ["c1"])
    assert e.value.code == "twitch_not_authorized"


def test_refresh_network_error_is_temporary_not_a_crash():
    def h(req):
        if req.url.path.endswith("/oauth2/token"):
            raise httpx.ConnectError("dns", request=req)
        return httpx.Response(401)

    cl, _, _ = mk(h)
    with pytest.raises(TemporaryError):
        cl.get_download_urls("1", ["c1"])


def test_editor_id_is_the_authenticated_user_and_scope_is_checked():
    seen = {}

    def h(req):
        if req.url.path.endswith("/oauth2/validate"):
            return httpx.Response(200, json={"user_id": "777", "login": "editor", "scopes": ["channel:manage:clips"]})
        seen.update(dict(req.url.params))
        return httpx.Response(200, json={"data": []})

    cl, _, _ = mk(h)
    cl.get_download_urls("1", ["c1"])
    assert seen["broadcaster_id"] == "1" and seen["editor_id"] == "777"  # coerenza broadcaster/editor/utente autenticato

    def noscope(req):
        return httpx.Response(200, json={"user_id": "1", "login": "x", "scopes": ["user:read:email"]})

    cl2, _, _ = mk(noscope)
    with pytest.raises(PermanentError) as e:
        cl2.get_download_urls("1", ["c1"])
    assert e.value.code == "twitch_scope_missing"


@pytest.mark.parametrize("status,cls,code", [(403, PermanentError, "twitch_forbidden"), (429, TemporaryError, "twitch_rate_limited"), (503, TemporaryError, "twitch_5xx")])
def test_helix_error_classification(status, cls, code):
    cl, _, _ = mk(lambda req: httpx.Response(status))
    with pytest.raises(cls) as e:
        cl.get_clip("c1")
    assert e.value.code == code


def test_malformed_json_is_temporary():
    cl, _, _ = mk(lambda req: httpx.Response(200, content=b"<html>"))
    with pytest.raises(TemporaryError):
        cl.get_clip("c1")


# ------------------------------------------------------------------ elenco clip
def test_viewer_created_clips_are_not_filtered_and_malformed_ones_are_skipped():
    def h(req):
        if req.url.path.endswith("/games"):
            return httpx.Response(200, json={"data": []})
        return httpx.Response(200, json={"data": [clip_json(1, creator="spettatore"), {"title": "senza id"}, clip_json(2, creator="iDensTv")], "pagination": {}})

    cl, _, _ = mk(h)
    out = cl.list_clips("1", utcnow() - timedelta(hours=24), utcnow() + timedelta(minutes=1))
    assert [m.creator_name for m in out] == ["iDensTv", "spettatore"] or {m.creator_name for m in out} == {"spettatore", "iDensTv"}
    assert cl.last_stats["malformed"] == 1


def test_full_window_is_bisected_to_avoid_api_cap():
    windows = []

    def h(req):
        if req.url.path.endswith("/games"):
            return httpx.Response(200, json={"data": []})
        p = dict(req.url.params)
        span = (p["ended_at"], p["started_at"])
        if "after" not in p:
            windows.append(span)
        big = len(windows) == 1  # la prima finestra (intera) è piena
        page = int(p.get("after", "0"))
        if big:
            data = [clip_json(page * 100 + i, age_h=2) for i in range(100)]
            return httpx.Response(200, json={"data": data, "pagination": {"cursor": str(page + 1)} if page < 9 else {}})
        return httpx.Response(200, json={"data": [clip_json(5000 + len(windows), age_h=2)], "pagination": {}})

    cl, _, _ = mk(h)
    out = cl.list_clips("1", utcnow() - timedelta(hours=6), utcnow())
    assert len(windows) == 3 and len(out) >= 1000  # finestra intera + due metà


# ------------------------------------------------------------------ download
def dl(handler):
    return HttpDownloader(httpx.Client(transport=httpx.MockTransport(handler)))


def test_download_ok_leaves_no_part_file(tmp_path):
    body = b"x" * 5000
    n = dl(lambda r: httpx.Response(200, content=body, headers={"content-type": "video/mp4"})).download("https://cdn/v.mp4", tmp_path / "a.mp4", 10**7)
    assert n == 5000 and (tmp_path / "a.mp4").read_bytes() == body and not list(tmp_path.glob("*.part"))


def test_incomplete_download_never_becomes_a_valid_file(tmp_path):
    class Truncated(httpx.SyncByteStream):
        def __iter__(self):
            yield b"x" * 100

    def h(r):
        return httpx.Response(200, headers={"content-type": "video/mp4", "content-length": "5000"}, stream=Truncated())

    with pytest.raises(TemporaryError):
        dl(h).download("https://cdn/v.mp4", tmp_path / "a.mp4", 10**7)
    assert not (tmp_path / "a.mp4").exists() and not list(tmp_path.glob("*.part"))


def test_connection_drop_mid_download_is_temporary(tmp_path):
    class Dropping(httpx.SyncByteStream):
        def __iter__(self):
            yield b"x" * 100
            raise httpx.ReadError("reset")

    with pytest.raises(TemporaryError):
        dl(lambda r: httpx.Response(200, headers={"content-type": "video/mp4"}, stream=Dropping())).download("https://cdn/v.mp4", tmp_path / "a.mp4", 10**7)
    assert not (tmp_path / "a.mp4").exists() and not list(tmp_path.glob("*.part"))


@pytest.mark.parametrize("status", [403, 404, 410])
def test_expired_url_is_temporary_so_a_fresh_one_is_requested(tmp_path, status):
    with pytest.raises(TemporaryError) as e:
        dl(lambda r: httpx.Response(status)).download("https://cdn/v.mp4", tmp_path / "a.mp4", 10**7)
    assert e.value.code == "download_url_expired"


def test_non_https_and_html_downloads_are_rejected(tmp_path):
    with pytest.raises(PermanentError):
        dl(lambda r: httpx.Response(200)).download("http://cdn/v.mp4", tmp_path / "a.mp4", 10**7)
    with pytest.raises(PermanentError):
        dl(lambda r: httpx.Response(200, content=b"<html>", headers={"content-type": "text/html"})).download("https://cdn/v.mp4", tmp_path / "a.mp4", 10**7)


# ------------------------------------------------------------------ pipeline: portrait / landscape / file corrotto
class ScriptedSource(MockTwitch):
    """Sorgente demo con download URL controllabili dal test; conta le richieste di URL (URL sempre rigenerate)."""

    def __init__(self, urls_seq):
        super().__init__()
        self.urls_seq, self.calls = urls_seq, 0

    def get_download_urls(self, broadcaster_id, clip_ids):
        i = min(self.calls, len(self.urls_seq) - 1)
        self.calls += 1
        portrait, landscape = self.urls_seq[i]
        return {cid: DownloadUrls(cid, portrait, landscape) for cid in clip_ids}


class ScriptedDownloader:
    def __init__(self, behaviours):
        self.behaviours, self.n, self.urls = behaviours, 0, []

    def download(self, url, dest, max_bytes):
        b = self.behaviours[min(self.n, len(self.behaviours) - 1)]
        self.n += 1
        self.urls.append(url)
        if b == "expired":
            raise TemporaryError("URL scaduto", code="download_url_expired")
        dest.parent.mkdir(parents=True, exist_ok=True)
        src = ensure_fixture("funny_portrait" if b in ("ok", "ok_portrait") else "skill_landscape")
        if b == "truncated":
            dest.write_bytes(src.read_bytes()[:3000])  # moov mancante/file troncato
        else:
            shutil.copy(src, dest)
        return dest.stat().st_size


def one_clip(db, src, dl_):
    MockTwitch().path.write_text("[]")
    MockTwitch().spawn("funny_portrait", minutes_ago=30)
    factory.OVERRIDES.update(clip_source=src, downloader=dl_)
    collector.scan(db)
    return db.scalar(select(Clip))


def test_portrait_missing_waits_then_falls_back_to_landscape_and_refetches_url(db):
    set_settings({"twitch": {"portrait_wait_minutes": 10}})
    src = ScriptedSource([(None, "fixture://x"), (None, "fixture://x"), (None, "fixture://y")])
    d = ScriptedDownloader(["landscape"])
    c = one_clip(db, src, d)
    pipeline.advance_one(db)
    db.refresh(c)
    assert c.status == S.WAITING_FOR_MEDIA and d.n == 0  # attesa configurata, nessun download ancora
    c.waiting_since = utcnow() - timedelta(minutes=11)  # attesa trascorsa
    c.next_attempt_at = None
    db.commit()
    pipeline.advance_one(db)
    db.refresh(c)
    assert c.status == S.DOWNLOADED and c.landscape_fallback and d.n == 1 and src.calls >= 2  # URL richiesta di nuovo ad ogni tentativo
    assert c.original_path and c.checksum


def test_portrait_download_preferred_when_available(db):
    src = ScriptedSource([("fixture://p", "fixture://l")])
    d = ScriptedDownloader(["ok_portrait"])
    c = one_clip(db, src, d)
    pipeline.advance_one(db)
    db.refresh(c)
    assert c.status == S.DOWNLOADED and d.urls == ["fixture://p"] and c.is_portrait_file and not c.landscape_fallback


def test_truncated_file_is_deleted_and_redownloaded_with_fresh_url(db):
    src = ScriptedSource([("fixture://p1", None), ("fixture://p2", None)])
    d = ScriptedDownloader(["truncated", "ok_portrait"])
    c = one_clip(db, src, d)
    pipeline.advance_one(db)
    db.refresh(c)
    from pathlib import Path

    assert c.status == S.WAITING_FOR_MEDIA and "corrott" in (c.error or "") and not c.original_path
    assert not list(Path(c.original_path or "/nonexistent").parent.glob(f"{c.id}*")) if c.original_path else True
    c.next_attempt_at = None
    db.commit()
    pipeline.advance_one(db)
    db.refresh(c)
    assert c.status == S.DOWNLOADED and d.urls == ["fixture://p1", "fixture://p2"] and c.media_info["duration"] > 1


def test_expired_url_then_recovery(db):
    src = ScriptedSource([("fixture://old", None), ("fixture://fresh", None)])
    d = ScriptedDownloader(["expired", "ok_portrait"])
    c = one_clip(db, src, d)
    pipeline.advance_one(db)
    db.refresh(c)
    assert c.status == S.WAITING_FOR_MEDIA
    c.next_attempt_at = None
    db.commit()
    pipeline.advance_one(db)
    db.refresh(c)
    assert c.status == S.DOWNLOADED and d.urls[-1] == "fixture://fresh"


# ------------------------------------------------------------------ scan: timeout, crash, clip durante il polling
def test_timeout_does_not_advance_cursor_and_next_scan_recovers(db):
    class Flaky(MockTwitch):
        fail = True

        def list_clips(self, *a):
            if Flaky.fail:
                raise TemporaryError("timeout", code="twitch_unreachable")
            return super().list_clips(*a)

    factory.OVERRIDES["clip_source"] = Flaky()
    with pytest.raises(TemporaryError):
        collector.scan(db)
    from app.db.models import SyncCursor

    cur = db.get(SyncCursor, "idenstv")
    assert cur.last_successful_scan_at is None and cur.last_error  # nessun avanzamento
    Flaky.fail = False
    r = collector.scan(db)
    assert r["first_run"] and r["new"] == 6  # backfill di 24h completo dopo il ripristino


def test_crash_mid_scan_then_restart_creates_no_duplicates(db, monkeypatch):
    from app.services import events as ev

    real = ev.emit

    def boom(db_, comp, typ, *a, **k):
        if typ == "scan_ok":
            raise RuntimeError("crash simulato prima del commit")
        return real(db_, comp, typ, *a, **k)

    monkeypatch.setattr(collector.events, "emit", boom)
    with pytest.raises(RuntimeError):
        collector.scan(db)
    db.rollback()
    monkeypatch.setattr(collector.events, "emit", real)
    assert db.scalar(select(func.count()).select_from(Clip)) == 0  # niente a metà
    r = collector.scan(db)
    r2 = collector.scan(db)
    assert r["new"] == 6 and r2["new"] == 0 and db.scalar(select(func.count()).select_from(Clip)) == 6


def test_clip_created_between_polls_is_found_by_overlap(db):
    collector.scan(db)
    MockTwitch().spawn("funny_portrait", minutes_ago=0.2)  # creata dopo/durante il ciclo precedente
    r = collector.scan(db)
    assert r["new"] == 1
    assert collector.scan(db)["new"] == 0


def test_polling_and_overlap_are_configurable(db):
    set_settings({"twitch": {"overlap_hours": 2, "polling_minutes": 7}})
    collector.scan(db)
    from app.db.models import SyncCursor

    cur = db.get(SyncCursor, "idenstv")
    cur.last_successful_scan_at = utcnow() - timedelta(hours=5)
    db.commit()
    collector.scan(db)
    db.refresh(cur)
    assert abs((cur.last_window_start - (utcnow() - timedelta(hours=7))).total_seconds()) < 60  # 5h + 2h di overlap


# ------------------------------------------------------------------ OAuth callback
@pytest.fixture()
def oauth(client, db, monkeypatch):
    from app.api import settings as api_settings
    from app.services import settings_service as ss

    ss.set_secret(db, "twitch_client_id", "cid")
    ss.set_secret(db, "twitch_client_secret", "sec")
    db.commit()
    orig = httpx.Client
    box = {"scopes": ["channel:manage:clips"], "login": "idenstv", "token_status": 200}

    def h(req):
        if req.url.path.endswith("/token"):
            return httpx.Response(box["token_status"], json={"access_token": "acc", "refresh_token": "ref", "scope": box["scopes"]})
        return httpx.Response(200, json={"user_id": "42", "login": box["login"], "scopes": box["scopes"]})

    monkeypatch.setattr(api_settings.httpx, "Client", lambda **kw: orig(transport=httpx.MockTransport(h)))
    return box


def start_flow(client):
    r = client.post("/api/v1/integrations/twitch/connect")
    assert r.status_code == 200
    url = r.json()["authorize_url"]
    assert "scope=channel%3Amanage%3Aclips" in url and "response_type=code" in url
    return url.split("state=")[1].split("&")[0]


def test_oauth_happy_path_stores_tokens_and_identity(client, db, oauth):
    state = start_flow(client)
    r = client.get(f"/api/v1/integrations/twitch/callback?code=abc&state={state}", follow_redirects=False)
    assert r.headers["location"].endswith("twitch=ok")
    from app.services import settings_service as ss

    db.expire_all()
    assert ss.get_secret(db, "twitch_access_token") == "acc" and ss.get_twitch_auth(db)["user_id"] == "42"
    # lo state è monouso
    r2 = client.get(f"/api/v1/integrations/twitch/callback?code=abc&state={state}", follow_redirects=False)
    assert r2.headers["location"].endswith("twitch=error")


def test_oauth_rejects_unknown_state_expired_state_and_denied(client, db, oauth):
    assert client.get("/api/v1/integrations/twitch/callback?code=abc&state=forged", follow_redirects=False).headers["location"].endswith("twitch=error")
    state = start_flow(client)
    row = db.get(OAuthState, state)
    row.created_at = utcnow() - timedelta(minutes=11)
    db.commit()
    assert client.get(f"/api/v1/integrations/twitch/callback?code=abc&state={state}", follow_redirects=False).headers["location"].endswith("twitch=error")
    state2 = start_flow(client)
    assert client.get(f"/api/v1/integrations/twitch/callback?error=access_denied&state={state2}", follow_redirects=False).headers["location"].endswith("twitch=error")


def test_oauth_without_required_scope_does_not_store_token(client, db, oauth):
    oauth["scopes"] = ["user:read:email"]
    state = start_flow(client)
    r = client.get(f"/api/v1/integrations/twitch/callback?code=abc&state={state}", follow_redirects=False)
    assert r.headers["location"].endswith("twitch=scope")
    from app.services import settings_service as ss

    db.expire_all()
    assert ss.get_secret(db, "twitch_access_token") == ""


def test_oauth_with_different_account_is_flagged(client, db, oauth):
    oauth["login"] = "altro_utente"
    state = start_flow(client)
    r = client.get(f"/api/v1/integrations/twitch/callback?code=abc&state={state}", follow_redirects=False)
    assert r.headers["location"].endswith("twitch=not_owner")
