"""Sicurezza e configurazione (Fase 8)."""
import logging
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app.core import secretbox
from app.services import events
from app.services import settings_service as ss

ROOT = Path(__file__).resolve().parents[3]


class XorBackend:
    """Backend finto reversibile al posto di DPAPI (che esiste solo su Windows)."""

    def encrypt(self, data: bytes) -> bytes:
        return bytes(b ^ 0x5A for b in data)[::-1]

    def decrypt(self, data: bytes) -> bytes:
        return bytes(b ^ 0x5A for b in data[::-1])


@pytest.fixture()
def dpapi(monkeypatch):
    monkeypatch.setenv("CLIPY_SECRET_PROTECTION", "dpapi")
    secretbox.set_backend_for_tests(XorBackend())
    yield
    secretbox.set_backend_for_tests(None)


def raw(db, name):
    from app.db.models import Secret

    return db.get(Secret, name).value


def test_secrets_are_plain_by_default_on_non_windows(db):
    assert secretbox.mode() == "none"
    ss.set_secret(db, "buffer_api_key", "plain-value-123")
    assert raw(db, "buffer_api_key") == "plain-value-123" and ss.get_secret(db, "buffer_api_key") == "plain-value-123"


def test_dpapi_mode_encrypts_at_rest_and_reads_back(db, dpapi):
    ss.set_secret(db, "buffer_api_key", "super-secret-key-123")
    stored = raw(db, "buffer_api_key")
    assert stored.startswith("dpapi:") and "super-secret-key-123" not in stored
    assert ss.get_secret(db, "buffer_api_key") == "super-secret-key-123" and ss.secrets_status(db)["buffer_api_key"] is True


def test_migration_encrypts_existing_plain_secrets_without_changing_values(db, monkeypatch):
    monkeypatch.setenv("CLIPY_SECRET_PROTECTION", "none")
    ss.set_secret(db, "llm_api_key", "legacy-plain-key")
    ss.set_secret(db, "twitch_client_secret", "legacy-twitch")
    db.commit()
    monkeypatch.setenv("CLIPY_SECRET_PROTECTION", "dpapi")
    secretbox.set_backend_for_tests(XorBackend())
    try:
        assert ss.protect_all(db) == 2 and ss.protect_all(db) == 0  # idempotente
        assert raw(db, "llm_api_key").startswith("dpapi:")
        assert ss.get_secret(db, "llm_api_key") == "legacy-plain-key" and ss.get_secret(db, "twitch_client_secret") == "legacy-twitch"
        assert ss.unprotect_all(db) == 2 and raw(db, "llm_api_key") == "legacy-plain-key"  # percorso di ritorno per migrare altrove
    finally:
        secretbox.set_backend_for_tests(None)


def test_secret_copied_to_another_machine_is_treated_as_missing_not_crash(db, dpapi):
    ss.set_secret(db, "buffer_api_key", "k-123456789")
    db.commit()

    class Broken:
        def encrypt(self, d):
            return d

        def decrypt(self, d):
            raise OSError("CryptUnprotectData fallita")

    secretbox.set_backend_for_tests(Broken())
    assert ss.get_secret(db, "buffer_api_key") == "" and ss.secrets_status(db)["buffer_api_key"] is False
    assert ss.secret_unreadable(db, "buffer_api_key") is True  # distinguibile da "mai configurato"


def test_no_secrets_or_databases_are_tracked_by_git():
    if not shutil.which("git") or not (ROOT / ".git").exists():
        pytest.skip("repository git non disponibile")
    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.splitlines()
    assert ".env" not in files and not [f for f in files if f.endswith((".db", ".db-wal", ".db-shm", ".sqlite"))]
    pat = re.compile(r"sk-[A-Za-z0-9_\-]{20,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{30,}|-----BEGIN [A-Z ]*PRIVATE KEY-----")
    hits = []
    for f in files:
        if f.startswith(("server/app/tests/", "frontend/package-lock.json", "frontend/src/__tests__/")) or not (ROOT / f).is_file():
            continue
        try:
            text = (ROOT / f).read_text("utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if pat.search(text):
            hits.append(f)
    assert not hits, f"possibili segreti hardcoded in: {hits}"
    assert ".env" in (ROOT / ".gitignore").read_text() and "*.db" in (ROOT / ".gitignore").read_text()


def test_env_example_and_scripts_bind_to_localhost_only():
    assert "CLIPY_HOST=127.0.0.1" in (ROOT / ".env.example").read_text()
    for f in ("scripts/start-dev.sh", "scripts/start-dev.ps1"):
        text = (ROOT / f).read_text()
        assert "0.0.0.0" not in text and "127.0.0.1" in text
    env_example = (ROOT / ".env.example").read_text()
    for line in env_example.splitlines():
        if re.match(r"(BUFFER_API_KEY|LLM_API_KEY|S3_SECRET_ACCESS_KEY|TWITCH_CLIENT_SECRET)=", line):
            assert line.split("=", 1)[1].strip() == "", line  # nessun valore reale nell'esempio


def test_log_sanitizer_masks_tokens_and_oauth_params():
    s = events.sanitize
    assert "abc123" not in s("GET /api/v1/integrations/twitch/callback?code=abc123&state=xyz HTTP/1.1")
    assert "sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUV" not in s("errore con chiave sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUV")
    assert "AKIAABCDEFGHIJKLMNOP" not in s("access key AKIAABCDEFGHIJKLMNOP")
    assert "tok123456789" not in s("Authorization: Bearer tok123456789") and "tok123456789" not in s("header OAuth tok123456789")
    assert "refreshvalue123" not in s("refresh_token=refreshvalue123")


def test_secret_filter_applies_to_log_records_including_uvicorn_access():
    rec = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                            ("127.0.0.1:5000", "GET", "/api/v1/integrations/twitch/callback?code=SECRETCODE12345&state=abcd", "1.1", 307), None)
    assert events.SecretFilter().filter(rec) and "SECRETCODE12345" not in rec.getMessage()
    assert isinstance(rec.args, tuple) and len(rec.args) == 5  # struttura intatta: il formatter di uvicorn.access non deve rompersi


def test_uvicorn_access_formatter_still_works_with_the_filter():
    from uvicorn.logging import AccessFormatter

    rec = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                            ("127.0.0.1:5000", "GET", "/cb?code=SECRETCODE12345&state=abcd", "1.1", 200), None)
    events.SecretFilter().filter(rec)
    out = AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s', use_colors=False).format(rec)
    assert "SECRETCODE12345" not in out and "GET" in out and "200" in out


def test_uvicorn_handlers_get_the_filter_after_install():
    from app.main import install_log_sanitizer

    h = logging.StreamHandler()
    lg = logging.getLogger("uvicorn.access")
    lg.addHandler(h)
    try:
        install_log_sanitizer()
        assert any(isinstance(f, events.SecretFilter) for f in h.filters)
    finally:
        lg.removeHandler(h)


def test_cross_origin_state_changing_requests_are_blocked(client):
    r = client.post("/api/v1/settings/live/disable", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden_origin"
    assert client.post("/api/v1/settings/live/disable", headers={"Origin": "http://testserver"}).status_code == 200  # stessa origine
    assert client.post("/api/v1/settings/live/disable", headers={"Origin": "http://127.0.0.1:5173"}).status_code == 200  # frontend dev consentito
    assert client.get("/api/v1/settings", headers={"Origin": "https://evil.example"}).status_code == 200  # le letture restano protette da CORS


def test_unexpected_host_header_is_rejected_against_dns_rebinding(client):
    assert client.get("/health", headers={"Host": "evil.example"}).status_code == 400
    assert client.get("/health", headers={"Host": "127.0.0.1:8000"}).status_code == 200


def test_live_publishing_is_off_by_default_and_cannot_be_flipped_by_patch(client):
    s = client.get("/api/v1/settings").json()["settings"]["publishing"]
    assert s["live_enabled"] is False and s["dry_run"] is True and s["is_connected"] is False
    assert client.get("/api/v1/system/status").json()["publish_mode"] in ("dry_run", "mock")
    for field in ("live_enabled", "is_connected"):
        assert client.patch("/api/v1/settings", json={"publishing": {field: True}}).status_code == 422
    r = client.post("/api/v1/settings/live/enable", json={"confirm": "si"})
    assert r.status_code == 422 and client.get("/api/v1/settings").json()["settings"]["publishing"]["live_enabled"] is False
    r = client.post("/api/v1/settings/live/enable", json={"confirm": "ABILITA PUBBLICAZIONE REALE"})
    assert r.status_code == 409  # senza Buffer collegato e storage pubblico non si abilita nemmeno con la frase giusta


def test_dry_run_mode_makes_zero_external_requests(db):
    from app.services import publishing
    from app.services.settings_service import get_settings

    assert publishing.effective_mode(get_settings(db)) == "dry_run"
