"""Adapter Buffer contro fixture che riproducono la forma delle risposte degli esempi ufficiali.
Test d'integrazione con MockTransport: verificano parsing, paginazione e gestione errori, NON la compatibilità live."""
import json
import re
from datetime import datetime

import httpx
import pytest

from app.adapters.publisher.base import PublishRequest
from app.adapters.publisher.buffer import BufferPublisher, q
from app.core.errors import IncompleteResult, PermanentError, TemporaryError

DUE = datetime(2026, 10, 23, 8, 0, 0)  # UTC naive


def gql(req: httpx.Request) -> str:
    return json.loads(req.content)["query"]


def client(handler, org="") -> BufferPublisher:
    return BufferPublisher("sk-secret-key-123456", http=httpx.Client(transport=httpx.MockTransport(handler)), organization_id=org)


def ok(data) -> httpx.Response:
    return httpx.Response(200, json={"data": data})


ORGS = [{"id": "org1", "name": "Personale"}, {"id": "org2", "name": "Agenzia"}]
CHANNELS = {
    "org1": [{"id": "c-tt", "name": "idens", "service": "tiktok", "isDisconnected": False},
             {"id": "c-ig", "name": "idens_ig", "service": "instagram", "isDisconnected": False}],
    "org2": [{"id": "c-tt-old", "name": "vecchio", "service": "tiktok", "isDisconnected": True},
             {"id": "c-tt2", "name": "secondo", "service": "tiktok", "isDisconnected": False}],
}


def account_handler(req):
    query = gql(req)
    if "organizations" in query:
        return ok({"account": {"organizations": ORGS}})
    m = re.search(r'organizationId: "([^"]+)"', query)
    assert "channels(input" in query and m, query  # i canali si chiedono PER organizzazione
    return ok({"channels": CHANNELS[m.group(1)]})


def test_multiple_organizations_and_tiktok_channels():
    r = client(account_handler).test_connection()
    assert [o["id"] for o in r["organizations"]] == ["org1", "org2"]
    tt = [c for c in r["channels"] if c["service"] == "tiktok"]
    assert {c["id"] for c in tt} == {"c-tt", "c-tt-old", "c-tt2"} and r["tiktok_channels"] == 3
    assert all(c["organization_id"] for c in r["channels"])  # organization_id memorizzabile con channel_id
    by = {c["id"]: c for c in r["channels"]}
    assert by["c-tt2"]["organization_id"] == "org2" and by["c-tt2"]["eligible"] is True


def test_disconnected_and_non_tiktok_channels_are_not_eligible():
    by = {c["id"]: c for c in client(account_handler).test_connection()["channels"]}
    assert by["c-tt-old"]["disconnected"] is True and by["c-tt-old"]["eligible"] is False and "disconnesso" in by["c-tt-old"]["reason"]
    assert by["c-ig"]["eligible"] is False and "non TikTok" in by["c-ig"]["reason"]


def test_account_without_organizations_is_reported_not_crashed():
    r = client(lambda req: ok({"account": {"organizations": []}})).test_connection()
    assert r["channels"] == [] and "organizzazioni" in r["warnings"][0]


def test_schema_fallback_when_optional_channel_field_rejected():
    seen = []

    def h(req):
        query = gql(req)
        if "organizations" in query:
            return ok({"account": {"organizations": [ORGS[0]]}})
        seen.append(query)
        if "isDisconnected" in query:
            return httpx.Response(200, json={"errors": [{"message": 'Cannot query field "isDisconnected" on type "Channel".'}]})
        return ok({"channels": [{"id": "c-tt", "name": "x", "service": "tiktok"}]})

    c = client(h).test_connection()["channels"][0]
    assert len(seen) == 2 and c["disconnected_known"] is False and c["eligible"] is True


def posts_handler(total: int, page: int = 100, statuses_seen=None, always_more=False):
    nodes = [{"id": f"p{i}", "status": "scheduled", "dueAt": "2026-10-23T08:00:00.000Z" if i == 250 else "2026-11-01T08:00:00.000Z", "text": f"t{i}"} for i in range(total)]

    def h(req):
        query = gql(req)
        if "organizations" in query:
            return ok({"account": {"organizations": ORGS[:1]}})
        if "channels(input" in query:
            return ok({"channels": CHANNELS["org1"]})
        assert 'organizationId: "org1"' in query and "first: 100" in query
        if statuses_seen is not None:
            statuses_seen.append(re.search(r"status: \[([^\]]*)\]", query).group(1) if "status:" in query else None)
        m = re.search(r'after: "(\d+)"', query)
        start = int(m.group(1)) if m else 0
        chunk = nodes[start:start + page]
        end = start + page
        more = always_more or end < total
        return ok({"posts": {"edges": [{"node": n} for n in chunk], "pageInfo": {"endCursor": str(end if not always_more else start), "hasNextPage": more}}})

    return h


def test_count_scheduled_paginates_all_pages():
    seen: list = []
    assert client(posts_handler(257, statuses_seen=seen), "org1").count_scheduled("c-tt") == 257  # 3 pagine, non solo 100
    assert len(seen) == 3 and seen[0] == "scheduled, sending"  # enum letterali, non stringhe


def test_find_posts_scans_every_page_and_matches_due_time():
    found = client(posts_handler(300), "org1").find_posts("c-tt", datetime(2026, 10, 23, 8, 0, 30))
    assert [p.id for p in found] == ["p250"]  # si trova nella 3ª pagina


def test_stuck_cursor_is_incomplete_not_empty():
    with pytest.raises(IncompleteResult):
        client(posts_handler(300, always_more=True), "org1").find_posts("c-tt", DUE)


def test_org_is_resolved_from_channel_when_not_configured():
    assert client(posts_handler(5)).count_scheduled("c-tt") == 5


def test_create_video_post_uses_scheduled_automatic_enums_and_utc_due():
    captured = {}

    def h(req):
        captured["q"] = gql(req)
        return ok({"createPost": {"__typename": "PostActionSuccess", "post": {"id": "p9", "status": "scheduled", "dueAt": "2026-10-23T08:00:00.000Z", "text": 'Ciao "mondo"\nriga'}}})

    p = client(h).create_post(PublishRequest("c-tt", 'Ciao "mondo"\nriga', "https://cdn.example/a.mp4", DUE, "fp"))
    qq = captured["q"]
    assert "schedulingType: automatic" in qq and "mode: customScheduled" in qq  # enum veri, senza virgolette
    assert 'dueAt: "2026-10-23T08:00:00.000Z"' in qq and 'channelId: "c-tt"' in qq
    assert 'url: "https://cdn.example/a.mp4"' in qq and "videos" in qq
    assert r'Ciao \"mondo\"\nriga' in qq  # testo escapato: niente iniezione
    assert p.id == "p9" and p.status == "scheduled" and p.due_at == DUE


def test_text_cannot_break_out_of_the_graphql_literal():
    evil = '"}) { __typename } mutation X { deletePost(id: "1") { id } } #'
    captured = {}

    def h(req):
        captured["q"] = gql(req)
        return ok({"createPost": {"__typename": "PostActionSuccess", "post": {"id": "p", "status": "scheduled"}}})

    client(h).create_post(PublishRequest("c-tt", evil, "https://x/y.mp4", DUE, "fp"))
    assert q(evil) in captured["q"]
    assert captured["q"].replace(q(evil), "").count("mutation") == 1  # il testo resta confinato nel letterale


def test_non_https_media_is_rejected_before_any_request():
    def h(req):
        raise AssertionError("nessuna richiesta attesa")

    with pytest.raises(PermanentError):
        client(h).create_post(PublishRequest("c-tt", "x", "http://cdn/a.mp4", DUE, "fp"))


def test_mutation_error_and_unknown_union_member():
    def err(req):
        return ok({"createPost": {"__typename": "MutationError", "message": "Canale non idoneo"}})

    with pytest.raises(PermanentError) as e:
        client(err).create_post(PublishRequest("c-tt", "x", "https://x/y.mp4", DUE, "fp"))
    assert e.value.code == "buffer_mutation_error" and "Canale non idoneo" in e.value.message and not e.value.ambiguous

    def other(req):
        return ok({"createPost": {"__typename": "LimitReachedError"}})

    with pytest.raises(PermanentError) as e2:
        client(other).create_post(PublishRequest("c-tt", "x", "https://x/y.mp4", DUE, "fp"))
    assert "LimitReachedError" in e2.value.message


@pytest.mark.parametrize("response,ambiguous", [
    (httpx.Response(502), True),
    (httpx.Response(200, content=b"<html>gateway</html>"), True),
    (httpx.Response(200, json={"data": None}), True),
    (httpx.Response(200, json={"data": {"createPost": None}}), True),
    (httpx.Response(200, json={"data": {"createPost": {"__typename": "PostActionSuccess", "post": None}}}), True),
    (httpx.Response(429, headers={"retry-after": "42"}), False),
])
def test_create_post_uncertain_outcomes_are_marked_ambiguous(response, ambiguous):
    with pytest.raises(TemporaryError) as e:
        client(lambda req: response).create_post(PublishRequest("c-tt", "x", "https://x/y.mp4", DUE, "fp"))
    assert e.value.ambiguous is ambiguous
    if response.status_code == 429:
        assert e.value.retry_after == 42 and e.value.code == "buffer_rate_limited"


def test_timeout_after_create_is_ambiguous_but_connection_refused_is_not():
    def timeout(req):
        raise httpx.ReadTimeout("slow", request=req)

    def dns(req):
        raise httpx.ConnectError("[Errno -2] Name or service not known", request=req)

    with pytest.raises(TemporaryError) as e:
        client(timeout).create_post(PublishRequest("c-tt", "x", "https://x/y.mp4", DUE, "fp"))
    assert e.value.ambiguous and e.value.code == "buffer_timeout"
    with pytest.raises(TemporaryError) as e2:
        client(dns).create_post(PublishRequest("c-tt", "x", "https://x/y.mp4", DUE, "fp"))
    assert not e2.value.ambiguous and e2.value.code == "buffer_unreachable"  # la richiesta non è mai partita


@pytest.mark.parametrize("response,cls,code", [
    (httpx.Response(401), PermanentError, "buffer_auth"),
    (httpx.Response(403), PermanentError, "buffer_auth"),
    (httpx.Response(400, text="bad"), PermanentError, "buffer_http_error"),
    (httpx.Response(500), TemporaryError, "buffer_5xx"),
    (httpx.Response(200, content=b"not json"), TemporaryError, "buffer_bad_response"),
    (httpx.Response(200, json={"data": None}), TemporaryError, "buffer_bad_response"),
    (httpx.Response(200, json={"errors": [{"message": "x", "extensions": {"code": "UNAUTHENTICATED"}}]}), PermanentError, "buffer_auth"),
    (httpx.Response(200, json={"errors": [{"message": "slow down", "extensions": {"code": "RATE_LIMITED"}}]}), TemporaryError, "buffer_rate_limited"),
    (httpx.Response(200, json={"errors": [{"message": "boom"}]}), PermanentError, "buffer_graphql"),
])
def test_query_error_classification(response, cls, code):
    with pytest.raises(cls) as e:
        client(lambda req: response).list_organizations()
    assert e.value.code == code


def test_graphql_400_with_errors_body_is_reported_as_graphql():
    r = httpx.Response(400, json={"errors": [{"message": "Syntax Error"}]})
    with pytest.raises(PermanentError) as e:
        client(lambda req: r).list_organizations()
    assert e.value.code == "buffer_graphql"


def test_errors_never_contain_the_api_key():
    r = httpx.Response(200, json={"errors": [{"message": "bad token sk-secret-key-123456 rejected"}]})
    with pytest.raises(PermanentError) as e:
        client(lambda req: r).list_organizations()
    assert "sk-secret-key-123456" not in e.value.message

    def boom(req):
        raise httpx.ReadError("conn reset for Bearer sk-secret-key-123456", request=req)

    with pytest.raises(TemporaryError) as e2:
        client(boom).list_organizations()
    assert "sk-secret-key-123456" not in e2.value.message


def test_get_post_existing_after_timeout_and_missing():
    def h(req):
        query = gql(req)
        assert 'post(input: { id: "p1" })' in query
        return ok({"post": {"id": "p1", "status": "sending", "dueAt": "2026-10-23T08:00:00.000Z", "text": "t"}})

    p = client(h).get_post("p1")
    assert p and p.status == "sending"  # 'sending' resta distinto da 'scheduled'/'sent'
    assert client(lambda req: ok({"post": None})).get_post("zz") is None


def test_get_post_falls_back_to_paginated_scan_when_single_query_mismatches():
    inner = posts_handler(10)

    def h(req):
        query = gql(req)
        if query.startswith("query GetPost "):
            return httpx.Response(200, json={"errors": [{"message": 'Unknown argument "input" on field "Query.post".'}]})
        return inner(req)

    assert client(h, "org1").get_post("p7").id == "p7"


def test_recover_existing_post_after_timeout_via_find_posts():
    """Scenario completo: createPost va in timeout ma il post esiste; la riconciliazione lo trova per dueAt."""
    created = {}

    def h(req):
        query = gql(req)
        if "createPost" in query:
            created["ok"] = True
            raise httpx.ReadTimeout("lost response", request=req)
        if "organizations" in query:
            return ok({"account": {"organizations": ORGS[:1]}})
        return ok({"posts": {"edges": [{"node": {"id": "px", "status": "scheduled", "dueAt": "2026-10-23T08:00:00.000Z", "text": "caption"}}],
                             "pageInfo": {"endCursor": None, "hasNextPage": False}}})

    c = client(h, "org1")
    with pytest.raises(TemporaryError) as e:
        c.create_post(PublishRequest("c-tt", "caption", "https://x/y.mp4", DUE, "fp"))
    assert e.value.ambiguous and created
    assert [p.id for p in c.find_posts("c-tt", DUE)] == ["px"]


def test_unsafe_identifiers_are_rejected():
    c = client(lambda req: ok({}), "org1")
    with pytest.raises(PermanentError):
        c.count_scheduled('x"]) { id } #')


def test_check_schema_reports_missing_pieces_without_side_effects():
    def h(req):
        query = gql(req)
        assert "mutation CreatePost" not in query and "createPost(input" not in query  # sola lettura
        if "__schema" in query:
            return ok({"__schema": {"queryType": {"fields": [{"name": "account"}, {"name": "channels"}, {"name": "posts"}]}, "mutationType": {"fields": [{"name": "createPost"}]}}})
        return ok({"__type": {"inputFields": [{"name": "text"}, {"name": "channelId"}, {"name": "schedulingType"}, {"name": "mode"}, {"name": "dueAt"}]}})

    r = client(h).check_schema()
    assert not r["ok"] and "Query mancante: post" in r["problems"] and "CreatePostInput senza campo: assets" in r["problems"]
