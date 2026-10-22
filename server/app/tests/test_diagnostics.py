"""Diagnostica integrazioni (Fase 7): stati distinti, test senza effetti collaterali, nessun segreto esposto."""
import json

import httpx
import pytest
from sqlalchemy import func, select

from app.adapters.ai.analyzers import LLMAnalyzer
from app.adapters.publisher.buffer import BufferPublisher
from app.db.models import Clip, PublicationJob
from app.services import factory
from app.services import settings_service as ss

from .conftest import set_settings
from .test_publishing import FakeStorage

SECRET = "sk-super-secret-value-9999"


def states(client):
    return {i["id"]: i for i in client.get("/api/v1/integrations/diagnostics").json()["items"]}


def test_default_states_distinguish_simulation_from_unconfigured(client):
    s = states(client)
    assert s["twitch"]["state"] == "NOT_CONFIGURED" and s["twitch"]["simulated"] is True
    assert s["buffer"]["state"] == "NOT_CONFIGURED" and s["buffer"]["simulated"] is False
    assert s["storage"]["state"] == "NOT_CONFIGURED" and s["storage"]["simulated"] is True
    assert s["llm"]["state"] == "NOT_CONFIGURED" and s["llm"]["simulated"] is True and "revisione" in s["llm"]["summary"]
    assert s["ffmpeg"]["state"] == "CONFIGURED" and s["whisper"]["state"] == "NOT_CONFIGURED"
    assert set(client.get("/api/v1/integrations/diagnostics").json()["states"]) == {"NOT_CONFIGURED", "CONFIGURED", "CONNECTED", "TEST_FAILED", "NEEDS_LIVE_VERIFICATION"}


def test_ffmpeg_test_is_real_and_connects(client):
    r = client.post("/api/v1/integrations/diagnostics/ffmpeg/test", json={}).json()
    assert r["state"] == "CONNECTED" and r["details"]["drawtext"] and r["details"]["h264_aac_encode"] and r["last_test"]["ok"]


def test_unknown_service_and_unconfigured_real_service(client):
    assert client.post("/api/v1/integrations/diagnostics/nope/test", json={}).status_code == 404
    assert client.post("/api/v1/integrations/diagnostics/buffer/test", json={}).status_code == 409  # nessuna API key: non si finge un esito


def test_demo_test_reports_simulation_not_success(client):
    r = client.post("/api/v1/integrations/diagnostics/twitch/test", json={}).json()
    assert r["state"] == "NOT_CONFIGURED" and r["simulated"] is True


def buffer_with(handler, queries):
    def h(req):
        q = json.loads(req.content)["query"]
        queries.append(q)
        return handler(q)

    return BufferPublisher(SECRET, http=httpx.Client(transport=httpx.MockTransport(h)), organization_id="")


SCHEMA_OK = {"__schema": {"queryType": {"fields": [{"name": n} for n in ("account", "channels", "posts", "post")]}, "mutationType": {"fields": [{"name": "createPost"}]}}}
TYPE_OK = {"__type": {"inputFields": [{"name": n} for n in ("text", "channelId", "schedulingType", "mode", "dueAt", "assets")]}}


def buffer_handler(channel_ok=True, schema=SCHEMA_OK):
    def h(q):
        if "organizations" in q:
            return httpx.Response(200, json={"data": {"account": {"organizations": [{"id": "o1", "name": "Org"}]}}})
        if "channels(input" in q:
            return httpx.Response(200, json={"data": {"channels": [{"id": "c1", "name": "tt", "service": "tiktok", "isDisconnected": not channel_ok}]}})
        if "__schema" in q:
            return httpx.Response(200, json={"data": schema})
        if "__type" in q:
            return httpx.Response(200, json={"data": TYPE_OK})
        raise AssertionError(q)

    return h


def configure_buffer(db, channel="c1"):
    set_settings({"publishing": {"channel_id": channel}})
    ss.set_secret(db, "buffer_api_key", SECRET)
    db.commit()


def test_buffer_connected_without_creating_any_post(client, db):
    configure_buffer(db)
    queries: list[str] = []
    factory.OVERRIDES["publisher"] = buffer_with(buffer_handler(), queries)
    jobs_before = db.scalar(select(func.count()).select_from(PublicationJob))
    r = client.post("/api/v1/integrations/diagnostics/buffer/test", json={}).json()
    assert r["state"] == "CONNECTED" and r["details"]["tiktok_channels"] == 1 and not r["simulated"]
    assert queries and not any("createPost(input" in q or q.lstrip().startswith("mutation") for q in queries)  # SOLA LETTURA
    assert db.scalar(select(func.count()).select_from(PublicationJob)) == jobs_before


def test_buffer_disconnected_channel_and_schema_mismatch_fail(client, db):
    configure_buffer(db)
    factory.OVERRIDES["publisher"] = buffer_with(buffer_handler(channel_ok=False), [])
    r = client.post("/api/v1/integrations/diagnostics/buffer/test", json={}).json()
    assert r["state"] == "TEST_FAILED" and "disconnesso" in r["last_test"]["message"]
    bad = {"__schema": {"queryType": {"fields": [{"name": "account"}]}, "mutationType": {"fields": []}}}
    factory.OVERRIDES["publisher"] = buffer_with(buffer_handler(schema=bad), [])
    r = client.post("/api/v1/integrations/diagnostics/buffer/test", json={}).json()
    assert r["state"] == "TEST_FAILED" and "Schema Buffer diverso" in r["last_test"]["message"]


def test_buffer_introspection_unavailable_means_needs_live_verification(client, db):
    configure_buffer(db)

    def h(q):
        if "__schema" in q or "__type" in q:
            return httpx.Response(200, json={"errors": [{"message": "GraphQL introspection is not allowed"}]})
        return buffer_handler()(q)

    factory.OVERRIDES["publisher"] = buffer_with(h, [])
    r = client.post("/api/v1/integrations/diagnostics/buffer/test", json={}).json()
    assert r["state"] == "NEEDS_LIVE_VERIFICATION"


def test_buffer_auth_failure_is_test_failed_without_leaking_key(client, db):
    configure_buffer(db)
    factory.OVERRIDES["publisher"] = buffer_with(lambda q: httpx.Response(401, text=SECRET), [])
    res = client.post("/api/v1/integrations/diagnostics/buffer/test", json={})
    r = res.json()
    assert r["state"] == "TEST_FAILED" and SECRET not in res.text


def test_result_is_invalidated_when_configuration_changes(client, db):
    configure_buffer(db)
    factory.OVERRIDES["publisher"] = buffer_with(buffer_handler(), [])
    assert client.post("/api/v1/integrations/diagnostics/buffer/test", json={}).json()["state"] == "CONNECTED"
    client.put("/api/v1/settings/secrets", json={"name": "buffer_api_key", "value": "another-key-123456"})
    s = states(client)["buffer"]
    assert s["state"] == "NOT_CONFIGURED" or s["state"] == "CONFIGURED"  # mai CONNECTED con la chiave nuova
    assert s["state"] != "CONNECTED"


def test_changing_buffer_key_disconnects_channel_and_forces_dry_run(client, db):
    configure_buffer(db)
    from .conftest import force_publishing

    force_publishing(is_connected=True, live_enabled=True, dry_run=False)
    client.put("/api/v1/settings/secrets", json={"name": "buffer_api_key", "value": "another-key-123456"})
    p = client.get("/api/v1/settings").json()["settings"]["publishing"]
    assert not p["is_connected"] and not p["live_enabled"] and p["dry_run"] and p["channel_id"] == ""


def configure_s3(db):
    set_settings({"storage": {"provider": "s3", "bucket": "b", "endpoint_url": "https://acc.r2.cloudflarestorage.com", "public_base_url": "https://cdn.example"}})
    ss.set_secret(db, "s3_access_key_id", "AKIAEXAMPLE")
    ss.set_secret(db, "s3_secret_access_key", SECRET)
    db.commit()


class TrackedStorage(FakeStorage):
    def __init__(self, public=True, delete_ok=True, gone_after_delete=True):
        super().__init__(public)
        self.deleted, self.delete_ok, self.gone = [], delete_ok, gone_after_delete

    def delete(self, key):
        from app.core.errors import TemporaryError

        if not self.delete_ok:
            raise TemporaryError("AccessDenied", code="storage_delete_failed")
        self.deleted.append(key)

    def verify_public(self, url):
        if self.deleted:
            return (not self.gone, "HTTP 404" if self.gone else "ancora presente")
        return super().verify_public(url)


def test_storage_test_needs_explicit_confirmation_and_cleans_up(client, db, env):
    configure_s3(db)
    st = TrackedStorage()
    factory.OVERRIDES["storage"] = st
    res = client.post("/api/v1/integrations/diagnostics/storage/test", json={})
    assert res.status_code == 422 and res.json()["error"]["code"] == "confirmation_required" and not st.uploaded  # niente upload senza conferma
    r = client.post("/api/v1/integrations/diagnostics/storage/test", json={"confirm": True}).json()
    assert r["state"] == "CONNECTED" and r["details"]["upload"] and r["details"]["anonymous_https"] and r["details"]["cleanup"] and r["details"]["cleanup_effective"]
    assert st.uploaded and st.deleted == st.uploaded and not list((env.data_dir / "temp").glob("storage-test-*"))


@pytest.mark.parametrize("kw,fragment", [({"public": False}, "anonimamente"), ({"delete_ok": False}, "cancellazione"), ({"gone_after_delete": False}, "cancellazione")])
def test_storage_failures_are_reported_honestly(client, db, kw, fragment):
    configure_s3(db)
    factory.OVERRIDES["storage"] = TrackedStorage(**kw)
    r = client.post("/api/v1/integrations/diagnostics/storage/test", json={"confirm": True}).json()
    assert r["state"] == "TEST_FAILED" and fragment in r["last_test"]["message"]


def llm_with(handler):
    return LLMAnalyzer("anthropic", SECRET, "m", http=httpx.Client(transport=httpx.MockTransport(handler)))


def configure_llm(db):
    set_settings({"ai": {"provider": "anthropic", "model": "claude-x"}})
    ss.set_secret(db, "llm_api_key", SECRET)
    db.commit()


def test_llm_test_requires_confirmation_then_connects_or_fails(client, db):
    configure_llm(db)
    calls = []

    def ok(req):
        calls.append(1)
        return httpx.Response(200, json={"content": [{"type": "text", "text": '{"ok": true}'}], "usage": {"input_tokens": 12, "output_tokens": 5}})

    factory.OVERRIDES["analyzer"] = llm_with(ok)
    assert client.post("/api/v1/integrations/diagnostics/llm/test", json={}).status_code == 422 and not calls
    r = client.post("/api/v1/integrations/diagnostics/llm/test", json={"confirm": True}).json()
    assert r["state"] == "CONNECTED" and r["details"]["tokens_in"] == 12
    factory.OVERRIDES["analyzer"] = llm_with(lambda req: httpx.Response(401, text="no"))
    r = client.post("/api/v1/integrations/diagnostics/llm/test", json={"confirm": True}).json()
    assert r["state"] == "TEST_FAILED"


def test_whisper_not_installed_is_reported_as_not_configured(client):
    r = client.post("/api/v1/integrations/diagnostics/whisper/test", json={"confirm": True}).json()
    assert r["state"] == "NOT_CONFIGURED" and "faster-whisper" in r["last_test"]["message"]


def test_diagnostics_and_settings_never_expose_secrets(client, db):
    configure_buffer(db)
    configure_llm(db)
    configure_s3(db)
    ss.set_secret(db, "twitch_client_secret", SECRET)
    ss.set_secret(db, "twitch_access_token", SECRET)
    db.commit()
    factory.OVERRIDES["publisher"] = buffer_with(buffer_handler(), [])
    client.post("/api/v1/integrations/diagnostics/buffer/test", json={})
    for url in ("/api/v1/integrations/diagnostics", "/api/v1/settings", "/api/v1/system/status", "/api/v1/system/events?limit=500"):
        assert SECRET not in client.get(url).text, url
    assert SECRET not in json.dumps(ss.secrets_status(db))


def test_diagnostic_tests_do_not_touch_clips_or_queue(client, db):
    client.post("/api/v1/clips/sync")
    before = (db.scalar(select(func.count()).select_from(Clip)), db.scalar(select(func.count()).select_from(PublicationJob)))
    for svc in ("twitch", "ffmpeg", "buffer"):
        client.post(f"/api/v1/integrations/diagnostics/{svc}/test", json={})
    db.expire_all()
    assert (db.scalar(select(func.count()).select_from(Clip)), db.scalar(select(func.count()).select_from(PublicationJob))) == before
