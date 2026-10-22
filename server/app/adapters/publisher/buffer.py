"""Publisher Buffer (GraphQL, https://api.buffer.com).

STATO DI VALIDAZIONE: allineato alle strutture descritte negli esempi ufficiali (get-organizations, get-channels,
get-paginated-posts, create-video-post, create-scheduled-post) *come riportati nel brief*; la documentazione non era
raggiungibile dal sandbox, quindi **NON VALIDATO LIVE**. Per verificarlo con un account reale senza creare post usa
`BufferPublisher.check_schema()` (introspezione, sola lettura) da Settings → Buffer → «Verifica schema».

Scelte di progetto:
- I valori dinamici sono inseriti come *letterali GraphQL* correttamente escapati (non come variabili): così non
  dipendiamo dai nomi dei tipi di input (`CreatePostInput`, `ChannelId`, ...) che non possiamo verificare, e gli enum
  (`schedulingType: automatic`, `mode: customScheduled`, `status: [scheduled]`) sono veri enum e non stringhe.
- Gerarchia: account → organizations → channels(input:{organizationId}) → posts(input:{organizationId, filter}).
- Le liste di campi richiesti hanno varianti di riserva: se lo schema rifiuta un campo opzionale si ritenta con meno campi.
- `createPost` NON è idempotente: ogni errore dopo l'invio della richiesta è marcato `ambiguous` e il chiamante deve
  riconciliare (find_posts) prima di riprovare.
- Nessun messaggio d'errore contiene la API key.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from datetime import UTC, datetime

import httpx

from app.adapters.publisher.base import PublishRequest, RemotePost
from app.core.errors import ClipyError, IncompleteResult, PermanentError, TemporaryError

ENDPOINT = "https://api.buffer.com"
MAX_PAGE_SIZE = 100
MAX_PAGES = 50  # tetto di sicurezza: oltre, la ricerca è dichiarata INCOMPLETA (mai "nessun risultato")
_ID_RE = re.compile(r"^[A-Za-z0-9_\-:.]{1,200}$")

Q_ORGANIZATIONS = "query GetOrganizations { account { organizations { id name } } }"

CHANNEL_FIELDS = ("id name service isDisconnected", "id name service")  # varianti (la 2ª se la 1ª è rifiutata)
POST_FIELDS = (
    "id status dueAt text externalLink error { message }",
    "id status dueAt text externalLink",
    "id status dueAt text",
)

_STATUS = {"sent": "sent", "error": "error", "scheduled": "scheduled", "sending": "sending", "draft": "draft", "needs_approval": "draft"}
_SCHEMA_ERR = re.compile(r"cannot query field|unknown (argument|type|field)|is not defined|unknown input|field .* is not defined", re.I)
_AUTH_CODES = {"UNAUTHENTICATED", "UNAUTHORIZED", "FORBIDDEN"}
_RATE_CODES = {"RATE_LIMITED", "RATE_LIMIT_EXCEEDED", "TOO_MANY_REQUESTS"}


def q(value: str) -> str:
    """Letterale stringa GraphQL sicuro (JSON è un sottoinsieme valido; niente interpolazione grezza)."""
    return json.dumps(value, ensure_ascii=False)


def _ident(value: str, what: str) -> str:
    if not value or not _ID_RE.match(value):
        raise PermanentError(f"{what} non valido", code="buffer_bad_id")
    return value


def _status(s: str | None) -> str:
    return _STATUS.get((s or "").lower(), "unknown")


def _parse_due(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(UTC).replace(tzinfo=None)
    except ValueError:
        return None


def _utc_literal(dt: datetime) -> str:
    d = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
    return d.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _norm_text(t: str) -> str:
    return re.sub(r"\s+", " ", t or "").strip()


class BufferPublisher:
    name = "buffer"
    simulated = False

    def __init__(self, api_key: str, http: httpx.Client | None = None, organization_id: str = ""):
        if not api_key:
            raise PermanentError("API key Buffer mancante", code="buffer_key_missing")
        self.key = api_key
        self.organization_id = organization_id
        self.http = http or httpx.Client(timeout=httpx.Timeout(30, connect=10))
        self._org_cache: dict[str, str] = {}  # channel_id -> organization_id
        self._ch_variant = 0
        self._post_variant = 0

    # ------------------------------------------------------------------ trasporto
    def _scrub(self, msg: str) -> str:
        return msg.replace(self.key, "***")[:400]

    def _gql(self, query: str, *, mutation: bool = False) -> dict:
        """Esegue una richiesta GraphQL. Per le mutation, ogni errore dopo l'invio è `ambiguous`."""
        try:
            r = self.http.post(ENDPOINT, json={"query": query}, headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"})
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:  # include errori DNS: la richiesta NON è partita
            raise TemporaryError(self._scrub(f"Buffer non raggiungibile (rete/DNS): {exc}"), code="buffer_unreachable") from exc
        except httpx.TimeoutException as exc:
            raise TemporaryError(self._scrub(f"Timeout Buffer: {exc}"), code="buffer_timeout", ambiguous=mutation) from exc
        except httpx.HTTPError as exc:  # reset, protocollo, lettura interrotta: potrebbe essere stata ricevuta
            raise TemporaryError(self._scrub(f"Errore di rete verso Buffer: {exc}"), code="buffer_network", ambiguous=mutation) from exc
        sc = r.status_code
        body = self._json(r)
        if sc in (401, 403):
            raise PermanentError("API key Buffer non valida, revocata o senza permessi", code="buffer_auth")
        if sc == 429:
            ra = r.headers.get("retry-after", "")
            raise TemporaryError("Rate limit Buffer: attendo prima di riprovare", code="buffer_rate_limited",
                                 retry_after=float(ra) if ra.replace(".", "", 1).isdigit() else None)
        if sc >= 500:
            raise TemporaryError(f"Buffer ha risposto {sc}", code="buffer_5xx", ambiguous=mutation)
        if sc >= 400 and not (isinstance(body, dict) and body.get("errors")):
            raise PermanentError(f"Richiesta rifiutata da Buffer (HTTP {sc})", code="buffer_http_error")
        if not isinstance(body, dict):
            raise TemporaryError("Risposta Buffer non è JSON valido", code="buffer_bad_response", ambiguous=mutation)
        data = body.get("data")
        errors = body.get("errors")
        if errors:
            self._raise_graphql(errors, data, mutation)
        if not isinstance(data, dict):
            raise TemporaryError("Risposta Buffer priva di 'data'", code="buffer_bad_response", ambiguous=mutation)
        return data

    @staticmethod
    def _json(r: httpx.Response):
        try:
            return r.json()
        except ValueError:
            return None

    def _raise_graphql(self, errors, data, mutation: bool) -> None:
        errs = errors if isinstance(errors, list) else [errors]
        codes = {str(((e or {}).get("extensions") or {}).get("code", "")).upper() for e in errs if isinstance(e, dict)}
        msg = self._scrub("; ".join(str((e or {}).get("message", "?")) if isinstance(e, dict) else str(e) for e in errs))
        if codes & _AUTH_CODES or re.search(r"unauthori[sz]ed|unauthenticated|invalid (api )?(key|token)", msg, re.I):
            raise PermanentError("API key Buffer non valida o senza permessi", code="buffer_auth")
        if codes & _RATE_CODES:
            raise TemporaryError("Rate limit Buffer (GraphQL)", code="buffer_rate_limited")
        if _SCHEMA_ERR.search(msg):
            raise PermanentError(f"Schema Buffer diverso dall'atteso: {msg}", code="buffer_schema_mismatch")
        # errori con dati parziali dopo una mutation: l'azione potrebbe essere stata eseguita
        partial = mutation and isinstance(data, dict) and any(v is not None for v in data.values())
        raise PermanentError(f"Errore GraphQL Buffer: {msg}", code="buffer_graphql", ambiguous=partial)

    def _variants(self, build: Callable[[str], str], variants: tuple[str, ...], attr: str) -> dict:
        """Prova le varianti di campi finché lo schema ne accetta una (ricorda l'ultima funzionante)."""
        start = getattr(self, attr)
        last: ClipyError | None = None
        for i in range(start, len(variants)):
            try:
                out = self._gql(build(variants[i]))
                setattr(self, attr, i)
                return out
            except PermanentError as exc:
                if exc.code != "buffer_schema_mismatch":
                    raise
                last = exc
        assert last is not None
        raise last

    # ------------------------------------------------------------------ organizzazioni / canali
    def list_organizations(self) -> list[dict]:
        data = self._gql(Q_ORGANIZATIONS)
        acct = data.get("account")
        if not isinstance(acct, dict) or not isinstance(acct.get("organizations"), list):
            raise TemporaryError("Risposta organizzazioni incompleta", code="buffer_bad_response")
        return [{"id": o["id"], "name": o.get("name") or o["id"]} for o in acct["organizations"] if isinstance(o, dict) and o.get("id")]

    def list_channels(self, organization_id: str) -> list[dict]:
        org = _ident(organization_id, "organizationId")
        data = self._variants(lambda f: f"query GetChannels {{ channels(input: {{ organizationId: {q(org)} }}) {{ {f} }} }}", CHANNEL_FIELDS, "_ch_variant")
        chans = data.get("channels")
        if not isinstance(chans, list):
            raise TemporaryError("Risposta canali incompleta", code="buffer_bad_response")
        out = []
        for c in chans:
            if not isinstance(c, dict) or not c.get("id"):
                continue
            self._org_cache[c["id"]] = org
            disc = c.get("isDisconnected")  # None = campo non disponibile nello schema
            service = (c.get("service") or "").lower()
            reason = None
            if disc:
                reason = "Canale disconnesso su Buffer: ricollegalo dal pannello Buffer."
            elif service != "tiktok":
                reason = f"Il canale è '{service or '?'}', non TikTok."
            out.append({"id": c["id"], "name": c.get("name"), "service": service, "type": c.get("type"), "disconnected": bool(disc),
                        "disconnected_known": disc is not None, "organization_id": org, "eligible": reason is None, "reason": reason})
        return out

    def test_connection(self) -> dict:
        """Sola lettura: organizzazioni e canali di TUTTE le organizzazioni (nessun post creato)."""
        orgs = self.list_organizations()
        chans: list[dict] = []
        for o in orgs:
            for c in self.list_channels(o["id"]):
                c["organization"] = o["name"]
                chans.append(c)
        warnings = []
        if not orgs:
            warnings.append("L'account Buffer non ha organizzazioni.")
        elif not any(c["service"] == "tiktok" for c in chans):
            warnings.append("Nessun canale TikTok collegato a Buffer.")
        return {"ok": True, "simulated": False, "organizations": orgs, "channels": chans, "warnings": warnings,
                "tiktok_channels": sum(1 for c in chans if c["service"] == "tiktok")}

    def _org_for(self, channel_id: str) -> str:
        if self.organization_id:
            return self.organization_id
        if channel_id in self._org_cache:
            return self._org_cache[channel_id]
        for o in self.list_organizations():
            if any(c["id"] == channel_id for c in self.list_channels(o["id"])):
                return o["id"]
        raise PermanentError("Canale non trovato in nessuna organizzazione Buffer", code="buffer_channel_not_found")

    # ------------------------------------------------------------------ post: lettura
    def _posts_page(self, org: str, channel_id: str, statuses: list[str] | None, after: str | None) -> dict:
        flt = f"channelIds: [{q(_ident(channel_id, 'channelId'))}]"
        if statuses:
            flt += ", status: [" + ", ".join(statuses) + "]"  # enum letterali (nessun valore libero: lista chiusa)
        arg_after = f", after: {q(after)}" if after else ""

        def build(fields: str) -> str:
            return (f"query GetPosts {{ posts(input: {{ organizationId: {q(org)}, filter: {{ {flt} }} }}, first: {MAX_PAGE_SIZE}{arg_after}) "
                    f"{{ edges {{ node {{ {fields} }} }} pageInfo {{ endCursor hasNextPage }} }} }}")

        return self._variants(build, POST_FIELDS, "_post_variant")

    def _iter_posts(self, channel_id: str, statuses: list[str] | None, max_pages: int = MAX_PAGES) -> Iterator[dict]:
        org = self._org_for(channel_id)
        after: str | None = None
        for _ in range(max_pages):
            conn = self._posts_page(org, channel_id, statuses, after).get("posts")
            if not isinstance(conn, dict) or not isinstance(conn.get("edges"), list):
                raise TemporaryError("Risposta posts incompleta", code="buffer_bad_response")
            for e in conn["edges"]:
                node = (e or {}).get("node")
                if isinstance(node, dict) and node.get("id"):
                    yield node
            info = conn.get("pageInfo") or {}
            nxt = info.get("endCursor")
            if not info.get("hasNextPage"):
                return
            if not nxt or nxt == after:
                raise IncompleteResult("Paginazione Buffer non avanza: elenco post incompleto", code="remote_search_incomplete")
            after = nxt
        raise IncompleteResult(f"Più di {max_pages * MAX_PAGE_SIZE} post: ricerca incompleta, verifica manuale", code="remote_search_incomplete")

    @staticmethod
    def _node(n: dict) -> RemotePost:
        return RemotePost(n["id"], _status(n.get("status")), _parse_due(n.get("dueAt")), n.get("text") or "",
                          n.get("externalLink"), (n.get("error") or {}).get("message") if isinstance(n.get("error"), dict) else None)

    def get_post(self, remote_id: str) -> RemotePost | None:
        rid = _ident(remote_id, "post id")
        try:
            data = self._variants(lambda f: f"query GetPost {{ post(input: {{ id: {q(rid)} }}) {{ {f} }} }}", POST_FIELDS, "_post_variant")
        except PermanentError as exc:
            if exc.code != "buffer_schema_mismatch" or not self.organization_id:
                raise
            return self._find_by_id_scan(rid)  # la query singola non combacia con lo schema: scansione paginata
        n = data.get("post")
        return self._node(n) if isinstance(n, dict) and n.get("id") else None

    def _find_by_id_scan(self, rid: str) -> RemotePost | None:
        for c in self.list_channels(self.organization_id):
            for n in self._iter_posts(c["id"], None):
                if n["id"] == rid:
                    return self._node(n)
        return None

    def find_posts(self, channel_id: str, due_at: datetime) -> list[RemotePost]:
        """Post del canale con dueAt entro ±120 s. Solleva IncompleteResult se non ha potuto leggere tutto."""
        out = [self._node(n) for n in self._iter_posts(channel_id, None)]
        return [p for p in out if p.due_at and abs((p.due_at - due_at).total_seconds()) < 120]

    def count_scheduled(self, channel_id: str) -> int:
        return sum(1 for _ in self._iter_posts(channel_id, ["scheduled", "sending"]))

    # ------------------------------------------------------------------ post: creazione
    def create_post(self, req: PublishRequest) -> RemotePost:
        if not req.video_url.startswith("https://"):
            raise PermanentError("Buffer richiede un URL video pubblico HTTPS", code="buffer_bad_media_url")
        mutation = (
            "mutation CreatePost { createPost(input: { "
            f"text: {q(req.text)}, channelId: {q(_ident(req.channel_id, 'channelId'))}, "
            "schedulingType: automatic, mode: customScheduled, "
            f"dueAt: {q(_utc_literal(req.due_at))}, "
            f"assets: {{ videos: [{{ url: {q(req.video_url)} }}] }} "
            "}) { __typename ... on PostActionSuccess { post { id status dueAt text } } ... on MutationError { message } } }"
        )
        data = self._gql(mutation, mutation=True)
        res = data.get("createPost")
        if not isinstance(res, dict):
            raise TemporaryError("Risposta createPost vuota: esito incerto", code="buffer_bad_response", ambiguous=True)
        typename = res.get("__typename")
        post = res.get("post")
        if typename == "PostActionSuccess" and isinstance(post, dict) and post.get("id"):
            return self._node(post)
        if typename == "PostActionSuccess":  # successo dichiarato ma senza ID: NON ritentare
            raise TemporaryError("createPost riuscito ma senza ID del post: riconciliazione necessaria", code="buffer_bad_response", ambiguous=True)
        msg = res.get("message") or f"tipo di risposta '{typename}'"
        raise PermanentError(f"Buffer ha rifiutato il post: {self._scrub(str(msg))}", code="buffer_mutation_error")

    # ------------------------------------------------------------------ diagnostica schema (sola lettura)
    def check_schema(self) -> dict:
        """Confronta lo schema reale (introspezione) con ciò che l'adapter usa. Non crea né modifica nulla."""
        try:
            data = self._gql("query { __schema { queryType { fields { name } } mutationType { fields { name } } } }")
        except PermanentError as exc:
            return {"ok": False, "checked": False, "message": f"Introspezione non disponibile: {exc.message}", "problems": []}
        sch = data.get("__schema") or {}
        qf = {f["name"] for f in (sch.get("queryType") or {}).get("fields", [])}
        mf = {f["name"] for f in (sch.get("mutationType") or {}).get("fields", [])}
        problems = [f"Query mancante: {n}" for n in ("account", "channels", "posts", "post") if n not in qf]
        problems += [f"Mutation mancante: {n}" for n in ("createPost",) if n not in mf]
        try:
            t = self._gql('query { __type(name: "CreatePostInput") { inputFields { name } } }').get("__type")
            if t:
                have = {f["name"] for f in t.get("inputFields", [])}
                problems += [f"CreatePostInput senza campo: {n}" for n in ("text", "channelId", "schedulingType", "mode", "dueAt", "assets") if n not in have]
            else:
                problems.append("Tipo CreatePostInput non trovato")
        except ClipyError as exc:
            problems.append(f"CreatePostInput non verificabile: {exc.message}")
        return {"ok": not problems, "checked": True, "problems": problems,
                "message": "Schema compatibile con l'adapter" if not problems else "Differenze rispetto all'adapter: correggere buffer.py prima del live"}


def same_text(a: str, b: str) -> bool:
    return _norm_text(a) == _norm_text(b)
