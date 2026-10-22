"""Publisher Buffer (GraphQL). NEEDS_LIVE_VERIFICATION: schema e nomi dei campi seguono il documento master;
la documentazione ufficiale non era raggiungibile in fase di sviluppo. Tutte le query sono in costanti per
poterle correggere in un solo punto dopo il test con account reale."""
from __future__ import annotations

from datetime import UTC, datetime

import httpx

from app.adapters.publisher.base import PublishRequest, RemotePost
from app.core.errors import PermanentError, TemporaryError

ENDPOINT = "https://api.buffer.com"

Q_CHANNELS = """query { account { organizations { id name channels { id name service type isDisconnected } } } }"""

M_CREATE_POST = """mutation CreatePost($input: CreatePostInput!) {
  createPost(input: $input) {
    __typename
    ... on PostActionSuccess { post { id status dueAt text } }
    ... on MutationError { message }
  }
}"""

Q_POSTS = """query Posts($channelId: ChannelId!, $status: [PostStatus!]) {
  posts(input: { filter: { channelIds: [$channelId], status: $status } }, first: 100) {
    edges { node { id status dueAt text error { message } externalLink } }
  }
}"""

Q_POST = """query Post($id: PostId!) { post(input: { id: $id }) { id status dueAt text error { message } externalLink } }"""

_STATUS = {"sent": "sent", "error": "error", "scheduled": "scheduled", "draft": "draft", "needs_approval": "draft", "sending": "scheduled"}


def _status(s: str | None) -> str:
    return _STATUS.get((s or "").lower(), "unknown")


def _parse_due(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(UTC).replace(tzinfo=None)


class BufferPublisher:
    name = "buffer"
    simulated = False

    def __init__(self, api_key: str, http: httpx.Client | None = None):
        if not api_key:
            raise PermanentError("API key Buffer mancante", code="buffer_key_missing")
        self.key = api_key
        self.http = http or httpx.Client(timeout=30)

    def _gql(self, query: str, variables: dict | None = None) -> dict:
        try:
            r = self.http.post(ENDPOINT, json={"query": query, "variables": variables or {}},
                               headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"})
        except httpx.TimeoutException as exc:
            raise TemporaryError(f"Timeout Buffer: {exc}", code="buffer_timeout") from exc
        except httpx.HTTPError as exc:
            raise TemporaryError(f"Buffer non raggiungibile: {exc}", code="buffer_unreachable") from exc
        if r.status_code in (401, 403):
            raise PermanentError("API key Buffer non valida o senza permessi", code="buffer_auth")
        if r.status_code == 429:
            raise TemporaryError("Rate limit Buffer", code="buffer_rate_limited")
        if r.status_code >= 500:
            raise TemporaryError(f"Buffer {r.status_code}", code="buffer_5xx")
        j = r.json()
        if j.get("errors"):
            msg = "; ".join(e.get("message", "?") for e in j["errors"])[:300]
            raise PermanentError(f"Errore GraphQL Buffer: {msg}", code="buffer_graphql")
        return j["data"]

    def test_connection(self) -> dict:
        data = self._gql(Q_CHANNELS)
        chans = []
        for org in data["account"]["organizations"]:
            for c in org.get("channels", []):
                chans.append({"id": c["id"], "name": c.get("name"), "service": c.get("service"), "type": c.get("type"),
                              "disconnected": c.get("isDisconnected", False), "organization": org.get("name")})
        return {"ok": True, "simulated": False, "channels": chans}

    def create_post(self, req: PublishRequest) -> RemotePost:
        inp = {"channelId": req.channel_id, "text": req.text, "schedulingType": "automatic", "mode": "customScheduled",
               "dueAt": req.due_at.replace(tzinfo=UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
               "assets": [{"video": {"url": req.video_url}}]}
        data = self._gql(M_CREATE_POST, {"input": inp})["createPost"]
        if data.get("__typename") == "MutationError" or "post" not in data:
            raise PermanentError(f"Buffer ha rifiutato il post: {data.get('message')}", code="buffer_mutation_error")
        p = data["post"]
        return RemotePost(p["id"], _status(p.get("status")), _parse_due(p.get("dueAt")), p.get("text", ""))

    def _node(self, n: dict) -> RemotePost:
        return RemotePost(n["id"], _status(n.get("status")), _parse_due(n.get("dueAt")), n.get("text", ""),
                          n.get("externalLink"), (n.get("error") or {}).get("message"))

    def get_post(self, remote_id: str) -> RemotePost | None:
        n = self._gql(Q_POST, {"id": remote_id}).get("post")
        return self._node(n) if n else None

    def find_posts(self, channel_id: str, due_at: datetime) -> list[RemotePost]:
        data = self._gql(Q_POSTS, {"channelId": channel_id, "status": None})
        out = [self._node(e["node"]) for e in data["posts"]["edges"]]
        return [p for p in out if p.due_at and abs((p.due_at - due_at).total_seconds()) < 120]

    def count_scheduled(self, channel_id: str) -> int:
        data = self._gql(Q_POSTS, {"channelId": channel_id, "status": ["scheduled"]})
        return len(data["posts"]["edges"])
