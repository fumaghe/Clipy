from datetime import timedelta

import httpx
from sqlalchemy import func, select

from app.adapters.twitch.base import DownloadUrls
from app.adapters.twitch.helix import HelixClient, TokenProvider
from app.adapters.twitch.mock import MockTwitch
from app.core.timeutil import utcnow
from app.db.models import Clip
from app.services import collector, factory, pipeline

from .conftest import set_settings


def count(db):
    return db.scalar(select(func.count()).select_from(Clip))


def test_two_scans_no_duplicates_and_bootstrap_24h(db):
    r1 = collector.scan(db)
    assert r1["first_run"] and r1["new"] == 6  # 6 clip nelle ultime 24h, quella di 4 giorni fa è esclusa
    r2 = collector.scan(db)
    assert r2["new"] == 0 and count(db) == 6
    assert db.scalar(select(func.count()).select_from(Clip).where(Clip.source_clip_id.like("%OLD%"))) == 0


def test_all_creators_imported_no_creator_filter(db):
    collector.scan(db)
    creators = {c.creator_name for c in db.scalars(select(Clip))}
    assert len(creators) >= 3 and "iDensTv" in creators and "viewer_marco" in creators


def test_new_clip_during_live_detected(db):
    collector.scan(db)
    MockTwitch().spawn("funny_portrait", minutes_ago=0.1)
    r = collector.scan(db)
    assert r["new"] == 1


def test_import_by_url_parses_id_only(db):
    MockTwitch().seed_if_empty()
    c = collector.import_clip(db, "https://www.twitch.tv/idenstv/clip/DemoClip0001-funny_portrait")
    assert c.source_clip_id == "DemoClip0001-funny_portrait"


def test_scan_failure_does_not_advance_cursor(db):
    from app.core.errors import TemporaryError

    class Boom(MockTwitch):
        def list_clips(self, *a):
            raise TemporaryError("down")

    factory.OVERRIDES["clip_source"] = Boom()
    try:
        collector.scan(db)
    except TemporaryError:
        pass
    from app.db.models import SyncCursor

    cur = db.get(SyncCursor, "idenstv")
    assert cur.last_successful_scan_at is None and cur.last_error
    factory.OVERRIDES["clip_source"] = MockTwitch()
    assert collector.scan(db)["new"] == 6  # nessuna clip persa


def test_portrait_preferred_and_landscape_waits_then_fallback(db):
    collector.scan(db)
    pipeline.run_until_idle(db, 40)
    c1 = db.scalar(select(Clip).where(Clip.source_clip_id.like("%funny_portrait")))
    assert c1.portrait_available and c1.is_portrait_file and not c1.landscape_fallback
    c2 = db.scalar(select(Clip).where(Clip.source_clip_id.like("%skill_landscape")))
    assert c2.status == "WAITING_FOR_MEDIA" and c2.portrait_available is False  # attende la portrait
    c2.waiting_since = utcnow() - timedelta(minutes=30)
    c2.next_attempt_at = None
    db.commit()
    pipeline.run_until_idle(db, 40)
    db.refresh(c2)
    assert c2.landscape_fallback and not c2.is_portrait_file and c2.render_path


def test_waiting_zero_minutes_goes_straight_to_fallback(db):
    set_settings({"twitch": {"portrait_wait_minutes": 0}})
    collector.scan(db)
    pipeline.run_until_idle(db, 60)
    c2 = db.scalar(select(Clip).where(Clip.source_clip_id.like("%skill_landscape")))
    assert c2.landscape_fallback


# ---- client Helix reale con trasporto mockato (paginazione, filtro created_at, split finestre)
def _helix(handler):
    http = httpx.Client(transport=httpx.MockTransport(handler))
    tokens = TokenProvider("cid", "sec", lambda: ("utok", "r"), lambda a, r: None, http=http)
    tokens._app = ("apptok", 9e12)
    return HelixClient(tokens, "cid", http=http)


def test_helix_pagination_and_window_split():
    calls = []
    now = utcnow()

    def clip(i, age_h):
        return {"id": f"c{i}", "broadcaster_id": "1", "broadcaster_name": "iDensTv", "creator_id": "9", "creator_name": f"v{i}", "game_id": "5",
                "title": "t", "url": "u", "created_at": (now - timedelta(hours=age_h)).strftime("%Y-%m-%dT%H:%M:%SZ"), "duration": 20, "view_count": i}

    def handler(req: httpx.Request):
        p = dict(req.url.params)
        if req.url.path.endswith("/games"):
            return httpx.Response(200, json={"data": [{"id": "5", "name": "GTA"}]})
        calls.append(p)
        if "after" not in p:
            return httpx.Response(200, json={"data": [clip(1, 1), clip(2, 2)], "pagination": {"cursor": "NEXT"}})
        return httpx.Response(200, json={"data": [clip(3, 3)], "pagination": {}})

    h = _helix(handler)
    out = h.list_clips("1", now - timedelta(days=10), now)
    assert {m.id for m in out} == {"c1", "c2", "c3"}  # id deduplicati tra le finestre
    assert len(calls) >= 4  # 2 finestre × 2 pagine
    assert all(m.game_name == "GTA" for m in out)


def test_helix_download_urls_batches_of_10_and_prefers_portrait():
    seen = []

    def handler(req):
        ids = req.url.params.get_list("clip_id")
        seen.append(len(ids))
        return httpx.Response(200, json={"data": [{"clip_id": i, "portrait_download_url": "https://p/" + i, "landscape_download_url": "https://l/" + i} for i in ids]})

    h = _helix(handler)
    out = h.get_download_urls("1", [f"c{i}" for i in range(23)])
    assert seen == [10, 10, 3] and out["c0"] == DownloadUrls("c0", "https://p/c0", "https://l/c0")
