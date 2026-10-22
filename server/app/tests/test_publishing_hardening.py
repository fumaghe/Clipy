"""Idempotenza, recovery e stati remoti (Fase 5). Integrazione con publisher/storage finti, nessuna rete."""
from datetime import timedelta

import pytest

from app.adapters.publisher.base import RemotePost
from app.core.errors import IncompleteResult, TemporaryError
from app.core.timeutil import utcnow
from app.domain import states as S
from app.services import factory, publishing, queue

from .test_publishing import FakePublisher, FakeStorage, prepare_live


class Flaky(FakePublisher):
    """create_post che 'perde' la risposta (il post esiste o no a seconda di `exists`)."""

    def __init__(self, exists=True, exc=None):
        super().__init__()
        self.exists = exists
        self.exc = exc or TemporaryError("HTTP 502", code="buffer_5xx", ambiguous=True)
        self.find_calls = 0
        self.find_error = None

    def create_post(self, req):
        self.create_calls += 1
        if self.exists:
            pid = f"b{len(self.posts) + 1}"
            self.posts[pid] = RemotePost(pid, "scheduled", req.due_at, req.text)
        raise self.exc

    def find_posts(self, ch, due):
        self.find_calls += 1
        if self.find_error:
            raise self.find_error
        return super().find_posts(ch, due)


def one_job(db, pub):
    prepare_live(db, 1)
    factory.OVERRIDES.update(publisher=pub, storage=FakeStorage())
    publishing.tick(db)
    return queue.list_jobs(db)[0]


def force_due(db, j):
    j.next_attempt_at = utcnow() - timedelta(seconds=1)
    db.commit()


def test_same_job_processed_twice_creates_once(db):
    """Due istanze con la stessa lista di job 'in scadenza': la seconda non deve rifare createPost."""
    pub = FakePublisher()
    prepare_live(db, 1)
    factory.OVERRIDES.update(publisher=pub, storage=FakeStorage())
    queue.rebuild(db)
    db.commit()
    jid = queue.list_jobs(db)[0].id
    publishing.process_job(db, jid)
    publishing.process_job(db, jid)  # selezione obsoleta: lo stato sotto lock è già SCHEDULED_REMOTE
    assert pub.create_calls == 1


def test_ambiguous_5xx_with_existing_post_is_adopted_not_duplicated(db):
    pub = Flaky(exists=True)
    j = one_job(db, pub)
    assert j.status == S.SCHEDULING and j.remote_state == S.REMOTE_SENDING and pub.create_calls == 1
    force_due(db, j)
    publishing.tick(db)
    db.refresh(j)
    assert j.status == S.SCHEDULED_REMOTE and j.remote_post_id and pub.create_calls == 1 and j.remote_state == S.REMOTE_SCHEDULED


def test_no_second_create_until_repeated_complete_empty_searches(db):
    pub = Flaky(exists=False)
    j = one_job(db, pub)
    # 1ª ricerca vuota: ancora nessuna nuova create
    force_due(db, j)
    publishing.tick(db)
    db.refresh(j)
    assert j.status == S.SCHEDULING and pub.create_calls == 1 and j.reconcile_empty_count == 1
    # 2ª ricerca vuota ma entro il periodo di grazia: ancora nessuna create
    force_due(db, j)
    publishing.tick(db)
    db.refresh(j)
    assert j.status == S.SCHEDULING and pub.create_calls == 1
    # grazia trascorsa + ricerche vuote ripetute → retry sicuro
    j.send_started_at = utcnow() - timedelta(minutes=10)
    force_due(db, j)
    publishing.tick(db)
    db.refresh(j)
    assert j.status in (S.RETRY_PENDING, S.SCHEDULING) and j.reconcile_empty_count >= 2


def test_incomplete_remote_search_requires_review_not_retry(db):
    pub = Flaky(exists=True)
    j = one_job(db, pub)
    pub.find_error = IncompleteResult("troppi post")
    force_due(db, j)
    publishing.tick(db)
    db.refresh(j)
    assert j.status == S.PUB_NEEDS_REVIEW and j.remote_state == S.REMOTE_UNCERTAIN and pub.create_calls == 1


def test_remote_post_with_different_text_at_same_time_needs_review(db):
    pub = Flaky(exists=False)
    j = one_job(db, pub)
    pub.posts["other"] = RemotePost("other", "scheduled", j.due_at, "un altro post dell'utente")
    force_due(db, j)
    publishing.tick(db)
    db.refresh(j)
    assert j.status == S.PUB_NEEDS_REVIEW and pub.create_calls == 1  # non si adotta un post altrui, non si duplica


def test_non_ambiguous_rejection_before_send_is_safe_to_retry(db):
    pub = Flaky(exists=False, exc=TemporaryError("429", code="buffer_rate_limited", retry_after=900))
    j = one_job(db, pub)
    assert j.status == S.RETRY_PENDING and j.remote_state == S.REMOTE_LOCAL
    assert j.next_attempt_at >= utcnow() + timedelta(seconds=800)  # Retry-After rispettato


def test_expired_slot_is_requeued_not_sent_in_the_past(db):
    pub = FakePublisher()
    prepare_live(db, 1)
    factory.OVERRIDES.update(publisher=pub, storage=FakeStorage())
    queue.rebuild(db)
    j = queue.list_jobs(db)[0]
    j.due_at = utcnow() - timedelta(minutes=30)
    db.commit()
    publishing.process_job(db, j.id)
    db.refresh(j)
    assert pub.create_calls == 0 and j.status == S.QUEUED_LOCAL
    queue.rebuild(db)
    db.refresh(j)
    assert j.due_at > utcnow()


def test_orphan_uploading_asset_is_resumed(db):
    pub = FakePublisher()
    prepare_live(db, 1)
    factory.OVERRIDES.update(publisher=pub, storage=FakeStorage())
    queue.rebuild(db)
    j = queue.list_jobs(db)[0]
    j.status = S.UPLOADING_ASSET  # processo morto durante l'upload, lock scaduto/assente
    db.commit()
    publishing.tick(db)
    db.refresh(j)
    assert j.status == S.SCHEDULED_REMOTE and pub.create_calls == 1


def test_remote_states_distinguish_buffer_sent_from_external_verification(db):
    pub = FakePublisher()
    prepare_live(db, 1)
    factory.OVERRIDES.update(publisher=pub, storage=FakeStorage())
    publishing.tick(db)
    j = queue.list_jobs(db)[0]
    assert j.status == S.SCHEDULED_REMOTE and j.remote_state == S.REMOTE_SCHEDULED and j.verification == S.VERIFY_NONE
    pub.posts["b1"].status = "sending"
    publishing.tick(db)
    db.refresh(j)
    assert j.status == S.SCHEDULED_REMOTE and j.remote_state == S.REMOTE_SENDING
    pub.posts["b1"].status = "sent"
    publishing.tick(db)
    db.refresh(j)
    assert j.status == S.PUBLISHED and j.remote_state == S.REMOTE_BUFFER_SENT and j.verification == S.VERIFY_NONE  # NON verificato
    publishing.mark_externally_verified(db, j.id, "visto sul profilo")
    assert j.remote_state == S.REMOTE_VERIFIED and j.verification == S.VERIFY_EXTERNAL and j.verified_at


def test_buffer_error_and_uncertain_states(db):
    pub = FakePublisher()
    prepare_live(db, 2)
    factory.OVERRIDES.update(publisher=pub, storage=FakeStorage())
    publishing.tick(db)
    a, b = queue.list_jobs(db)
    pub.posts[a.remote_post_id].status, pub.posts[a.remote_post_id].error = "error", "TikTok ha rifiutato"
    pub.posts[b.remote_post_id].status = "unknown"
    b.due_at = utcnow() - timedelta(hours=7)
    db.commit()
    publishing.tick(db)
    db.refresh(a)
    db.refresh(b)
    assert a.status == S.PUB_FAILED and a.remote_state == S.REMOTE_BUFFER_ERROR and "TikTok" in a.error
    assert b.status == S.PUB_NEEDS_REVIEW and b.remote_state == S.REMOTE_UNCERTAIN


def test_cannot_verify_simulated_or_unpublished_jobs(db):
    from app.core.errors import ClipyError

    pub = FakePublisher()
    prepare_live(db, 1)
    factory.OVERRIDES.update(publisher=pub, storage=FakeStorage())
    publishing.tick(db)
    with pytest.raises(ClipyError):
        publishing.mark_externally_verified(db, queue.list_jobs(db)[0].id)


def test_second_instance_does_not_clear_locks_of_live_leader(env):
    from app.db.base import session_scope
    from app.db.models import Clip, Lease
    from app.main import _recover

    with session_scope() as db:
        from app.adapters.twitch.base import ClipMeta
        from app.services import collector

        c, _ = collector.upsert_clip(db, ClipMeta("clip-aaa111", "1", "b", "2", "c", "", "", "t", "u", utcnow(), 10), demo=True)
        c.locked_until = utcnow() + timedelta(minutes=10)
        db.add(Lease(name="worker", owner="other-process", expires_at=utcnow() + timedelta(seconds=30)))
        db.flush()
        _recover(db)
        assert c.locked_until is not None  # il leader vivo sta lavorando: lock intatto
        db.get(Lease, "worker").expires_at = utcnow() - timedelta(seconds=1)  # leader morto
        _recover(db)
        db.refresh(c)
        assert db.get(Clip, c.id).locked_until is None


def test_migration_0002_upgrades_an_old_database_in_place(env):
    from sqlalchemy import inspect, text

    from app.db.base import get_engine, run_migrations

    eng = get_engine()
    with eng.begin() as conn:
        for col in ("remote_state", "verification", "verified_at", "send_started_at", "reconcile_empty_count"):
            conn.execute(text(f"ALTER TABLE publication_jobs DROP COLUMN {col}"))
        conn.execute(text("UPDATE alembic_version SET version_num='0001'"))
    run_migrations()
    cols = {c["name"] for c in inspect(get_engine()).get_columns("publication_jobs")}
    assert {"remote_state", "verification", "verified_at", "send_started_at", "reconcile_empty_count"} <= cols
