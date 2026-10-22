from sqlalchemy import select

from app.db.models import Secret
from app.services import factory

from .conftest import UniqueAnalyzer, force_publishing


def test_health_and_status(client):
    assert client.get("/health").json()["status"] == "ok"
    s = client.get("/api/v1/system/status").json()
    assert s["demo_mode"] is True and s["publish_mode"] == "dry_run" and s["live_enabled"] is False and s["ffmpeg"]


def test_secrets_never_returned_or_logged(client, db):
    r = client.put("/api/v1/settings/secrets", json={"name": "llm_api_key", "value": "sk-supersecret-12345678"})
    assert r.status_code == 200 and r.json()["secrets"]["llm_api_key"] is True
    body = client.get("/api/v1/settings").text
    assert "supersecret" not in body
    assert "supersecret" not in client.get("/api/v1/system/events?limit=100").text
    assert db.scalar(select(Secret).where(Secret.name == "llm_api_key")).value.startswith("sk-")
    assert client.put("/api/v1/settings/secrets", json={"name": "nope", "value": "x"}).status_code == 422


def test_protected_flags_not_patchable(client):
    for field in ("live_enabled", "is_connected"):
        r = client.patch("/api/v1/settings", json={"publishing": {field: True}})
        assert r.status_code == 422 and r.json()["error"]["code"] == "forbidden_field"


def test_invalid_settings_are_rejected_with_message(client):
    r = client.patch("/api/v1/settings", json={"publishing": {"slots": ["25:99"]}})
    assert r.status_code == 422 and "slot" in r.json()["error"]["message"]
    r = client.patch("/api/v1/settings", json={"publishing": {"daily_max": 0}})
    assert r.status_code == 422


def test_live_enable_requires_phrase_and_prerequisites(client):
    assert client.post("/api/v1/settings/live/enable", json={"confirm": "si"}).status_code == 422
    r = client.post("/api/v1/settings/live/enable", json={"confirm": "ABILITA PUBBLICAZIONE REALE"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "buffer_not_connected"
    force_publishing(is_connected=True, channel_id="c")
    r = client.post("/api/v1/settings/live/enable", json={"confirm": "ABILITA PUBBLICAZIONE REALE"})
    assert r.json()["error"]["code"] == "storage_not_configured"
    assert client.get("/api/v1/system/status").json()["live_enabled"] is False


def test_error_format_consistent(client):
    r = client.get("/api/v1/clips/does-not-exist")
    assert r.status_code == 404 and set(r.json()["error"]) == {"code", "message"}
    r = client.post("/api/v1/clips/import", json={"ref": "!!"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"
    r = client.post("/api/v1/clips/import", json={"ref": "not a clip id!"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_clip_ref"


def test_clip_endpoints_flow(client, db):
    from app.services import collector, pipeline

    factory.OVERRIDES["analyzer"] = UniqueAnalyzer()
    collector.scan(db)
    pipeline.run_until_idle(db, 60)
    items = client.get("/api/v1/clips?status=AUTO_READY").json()["items"]
    assert items
    cid = items[0]["id"]
    assert client.get(f"/api/v1/clips/{cid}/video?kind=render").headers["content-type"] == "video/mp4"
    assert client.get(f"/api/v1/clips/{cid}/preview").headers["content-type"] == "image/jpeg"
    r = client.patch(f"/api/v1/reviews/{cid}/content", json={"caption": "Nuova caption carrello"})
    assert r.status_code == 200 and r.json()["caption"] == "Nuova caption carrello"
    t = client.post("/api/v1/templates/default/preview", json={"text": "Ciao", "clip_id": cid})
    assert t.headers["content-type"] == "image/png" and t.content[:4] == b"\x89PNG"
    assert client.post("/api/v1/templates/default/preview", json={"config": {"font_size": 5}, "text": "x"}).status_code == 422


def test_template_validation_and_activation(client):
    t = client.get("/api/v1/templates").json()
    assert t["items"][0]["reference_status"] == "REFERENCE_NOT_VERIFIED"
    bad = client.put("/api/v1/templates/default", json={"name": "x", "config": {"duration": {"mode": "time_range", "start": 5, "end": 2}}})
    assert bad.status_code == 422


def test_retention_keeps_queued_files(db):
    from datetime import timedelta
    from pathlib import Path

    from app.core.timeutil import utcnow
    from app.services import collector, pipeline, retention

    collector.scan(db)
    pipeline.run_until_idle(db, 60)
    from app.db.models import Clip
    from app.services import reviews

    c = db.scalars(select(Clip).where(Clip.render_path.is_not(None))).first()
    reviews.reject(db, c.id)
    c.updated_at = utcnow() - timedelta(days=90)
    db.commit()
    other = [x for x in db.scalars(select(Clip).where(Clip.render_path.is_not(None))) if x.id != c.id][0]
    other.updated_at = utcnow() - timedelta(days=90)
    db.commit()
    r = retention.cleanup(db)
    db.commit()
    assert r["removed"] >= 1 and c.render_path is None and c.original_path is None
    assert other.render_path and Path(other.render_path).exists()  # idonea e non pubblicata: non si tocca
