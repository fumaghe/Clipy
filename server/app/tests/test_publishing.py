from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from app.adapters.publisher.base import PublishRequest
from app.adapters.publisher.buffer import BufferPublisher
from app.adapters.storage.base import StoredAsset
from app.core.errors import PermanentError
from app.core.timeutil import utcnow
from app.db.models import Clip, PublicationJob
from app.domain import states as S
from app.services import collector, factory, pipeline, publishing, queue

from .conftest import UniqueAnalyzer, force_publishing, set_settings


def buffer_client(handler):
    return BufferPublisher("key", http=httpx.Client(transport=httpx.MockTransport(handler)))


def test_buffer_create_post_success_and_mutation_error():
    def ok(req):
        body = req.read().decode()
        assert "customScheduled" in body and "automatic" in body
        return httpx.Response(200, json={"data": {"createPost": {"__typename": "PostActionSuccess", "post": {"id": "p1", "status": "scheduled", "dueAt": "2026-10-23T08:00:00.000Z", "text": "x"}}}})

    due = utcnow() + timedelta(hours=3)
    p = buffer_client(ok).create_post(PublishRequest("ch", "x", "https://u/v.mp4", due, "fp"))
    assert p.id == "p1" and p.status == "scheduled"

    def err(req):
        return httpx.Response(200, json={"data": {"createPost": {"__typename": "MutationError", "message": "no"}}})

    with pytest.raises(PermanentError):
        buffer_client(err).create_post(PublishRequest("ch", "x", "https://u/v.mp4", due, "fp"))


def test_buffer_auth_and_rate_limit_classification():
    from app.core.errors import TemporaryError

    with pytest.raises(PermanentError):
        buffer_client(lambda r: httpx.Response(401)).test_connection()
    with pytest.raises(TemporaryError):
        buffer_client(lambda r: httpx.Response(429)).test_connection()


class FakeStorage:
    name = "fake"

    def __init__(self, ok=True):
        self.ok, self.uploaded = ok, []

    def upload(self, path, key):
        self.uploaded.append(key)
        return StoredAsset(key, f"https://cdn.example/{key}", True)

    def verify_public(self, url):
        return (self.ok, "ok" if self.ok else "HTTP 403")

    def delete(self, key):
        pass


class FakePublisher:
    name, simulated = "buffer", False

    def __init__(self):
        self.posts, self.create_calls, self.fail_mode = {}, 0, None
        self.remote_scheduled = 0

    def create_post(self, req):
        from app.adapters.publisher.base import RemotePost
        from app.core.errors import TemporaryError

        self.create_calls += 1
        pid = f"b{len(self.posts) + 1}"
        self.posts[pid] = RemotePost(pid, "scheduled", req.due_at, req.text)
        if self.fail_mode == "timeout_after_create":
            raise TemporaryError("timeout", code="buffer_timeout")  # il post ESISTE ma la risposta è andata persa
        return self.posts[pid]

    def get_post(self, rid):
        return self.posts.get(rid)

    def find_posts(self, ch, due):
        return [p for p in self.posts.values() if abs((p.due_at - due).total_seconds()) < 120]

    def count_scheduled(self, ch):
        return self.remote_scheduled + sum(1 for p in self.posts.values() if p.status == "scheduled")

    def test_connection(self):
        return {"ok": True, "channels": []}


def prepare_live(db, n_clips=2):
    factory.OVERRIDES["analyzer"] = UniqueAnalyzer()
    force_publishing(provider="buffer", is_connected=True, channel_id="ch1", live_enabled=True, dry_run=False)
    from app.adapters.twitch.mock import MockTwitch

    t = MockTwitch()
    t.path.write_text("[]")
    for i in range(n_clips):
        t.spawn("funny_portrait", minutes_ago=100 + i)
    collector.scan(db)
    pipeline.run_until_idle(db, 80)
    db.commit()


def test_live_flow_scheduled_remote_not_published_until_confirmed(db):
    prepare_live(db)
    pub, st = FakePublisher(), FakeStorage()
    factory.OVERRIDES.update(publisher=pub, storage=st)
    publishing.tick(db)
    jobs = queue.list_jobs(db, [S.SCHEDULED_REMOTE])
    assert len(jobs) == 2 and pub.create_calls == 2
    assert all(j.remote_post_id and j.asset_url.startswith("https://cdn.example/") and len(j.asset_key) > 30 for j in jobs)  # nome opaco
    publishing.tick(db)
    assert pub.create_calls == 2  # idempotente: nessun nuovo create
    # Buffer conferma l'invio → PUBLISHED
    for p in pub.posts.values():
        p.status = "sent"
    publishing.tick(db)
    assert {j.status for j in queue.list_jobs(db)} == {S.PUBLISHED}


def test_crash_after_send_before_saving_id_reconciles_without_repost(db):
    prepare_live(db, 1)
    pub, st = FakePublisher(), FakeStorage()
    pub.fail_mode = "timeout_after_create"
    factory.OVERRIDES.update(publisher=pub, storage=st)
    publishing.tick(db)
    j = queue.list_jobs(db)[0]
    assert j.status == S.SCHEDULING and not j.remote_post_id and pub.create_calls == 1
    j.next_attempt_at = utcnow() - timedelta(seconds=1)
    db.commit()
    publishing.tick(db)
    db.refresh(j)
    assert j.status == S.SCHEDULED_REMOTE and j.remote_post_id == "b1" and pub.create_calls == 1  # nessun repost cieco


def test_ambiguous_remote_matches_go_to_needs_review(db):
    from app.adapters.publisher.base import RemotePost

    prepare_live(db, 1)
    pub, st = FakePublisher(), FakeStorage()
    pub.fail_mode = "timeout_after_create"
    factory.OVERRIDES.update(publisher=pub, storage=st)
    publishing.tick(db)
    j = queue.list_jobs(db)[0]
    pub.posts["dup"] = RemotePost("dup", "scheduled", j.due_at, pub.posts["b1"].text)
    j.next_attempt_at = utcnow() - timedelta(seconds=1)
    db.commit()
    publishing.tick(db)
    db.refresh(j)
    assert j.status == S.PUB_NEEDS_REVIEW and pub.create_calls == 1


def test_media_unreachable_no_false_publish_and_backoff(db):
    prepare_live(db, 1)
    pub, st = FakePublisher(), FakeStorage(ok=False)
    factory.OVERRIDES.update(publisher=pub, storage=st)
    publishing.tick(db)
    j = queue.list_jobs(db)[0]
    assert j.status == S.RETRY_PENDING and pub.create_calls == 0 and j.next_attempt_at > utcnow()
    assert j.attempts == 1 and "raggiungibile" in j.error
    publishing.tick(db)  # il backoff non è scaduto: nessun nuovo tentativo
    db.refresh(j)
    assert j.attempts == 1


def test_remote_limit_keeps_rest_in_local_queue(db):
    prepare_live(db, 3)
    set_settings({"publishing": {"remote_max_scheduled": 1}})
    pub, st = FakePublisher(), FakeStorage()
    factory.OVERRIDES.update(publisher=pub, storage=st)
    publishing.tick(db)
    statuses = sorted(j.status for j in queue.list_jobs(db))
    assert statuses.count(S.SCHEDULED_REMOTE) == 1 and statuses.count(S.QUEUED_LOCAL) == 2 and pub.create_calls == 1


def test_live_requires_public_storage(db):
    prepare_live(db, 1)
    factory.OVERRIDES["publisher"] = FakePublisher()
    publishing.tick(db)  # storage locale con modalità live → errore permanente, nessun post
    j = queue.list_jobs(db)[0]
    assert j.status == S.PUB_FAILED and "Storage" in j.error


def test_no_job_for_needs_review_without_approval(db):
    from app.adapters.twitch.mock import MockTwitch

    MockTwitch().path.write_text("[]")
    MockTwitch().spawn("blocked_personal", minutes_ago=30)
    collector.scan(db)
    pipeline.run_until_idle(db, 40)
    publishing.tick(db)
    assert not list(db.scalars(select(PublicationJob)))
    assert db.scalar(select(Clip)).status == S.NEEDS_REVIEW


def test_cancel_job_replans_slots(db):
    factory.OVERRIDES["analyzer"] = UniqueAnalyzer()
    from app.adapters.twitch.mock import MockTwitch

    t = MockTwitch()
    t.path.write_text("[]")
    for i in range(6):
        t.spawn("funny_portrait", minutes_ago=100 + i)
    collector.scan(db)
    pipeline.run_until_idle(db, 150)
    queue.rebuild(db)
    jobs = queue.list_jobs(db, [S.QUEUED_LOCAL])
    last = jobs[-1].due_at
    queue.update_job(db, jobs[0].id, cancel=True)
    queue.rebuild(db)
    after = queue.list_jobs(db, [S.QUEUED_LOCAL])
    assert len(after) == 5 and after[-1].due_at <= last  # i job successivi scalano negli slot liberati
    _ = StoredAsset
