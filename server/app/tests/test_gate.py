from app.core.settings_model import ReviewSettings
from app.domain.review_gate import GateInput, evaluate

OK_CHECKS = {"ok": True, "has_audio": True, "text_clipped": False}


def base(**kw):
    d = dict(transcript="Ho preso il volo con il carrello, incredibile", game_name="GTA", clip_title="Carrello", overlay_title="Il carrello ha preso il volo",
             caption="Quando la fisica non collabora col carrello", hashtags=["#gaming"], confidence=0.9, render_checks=OK_CHECKS, banned_words=["nazi"])
    d.update(kw)
    return GateInput(**d)


def test_safe_clip_auto_ready():
    r = evaluate(base(), ReviewSettings())
    assert r.state == "AUTO_READY" and not r.flags


def test_low_confidence_not_sole_signal():
    r = evaluate(base(confidence=0.2), ReviewSettings())
    assert "low_confidence" in r.flags and r.needs_review


def test_pii_and_banned_words():
    r = evaluate(base(transcript="scrivimi a mario@example.com il carrello"), ReviewSettings())
    assert "pii_detected" in r.flags
    r = evaluate(base(caption="il carrello nazi"), ReviewSettings())
    assert "banned_words" in r.flags


def test_no_evidence_needs_review_but_metadata_counts():
    r = evaluate(base(transcript="", game_name="", clip_title="", overlay_title="Boh", caption="Boh boh"), ReviewSettings())
    assert "insufficient_evidence" in r.flags
    r = evaluate(base(transcript="", overlay_title="Gta carrello pazzo", caption="Carrello"), ReviewSettings())
    assert "insufficient_evidence" not in r.flags  # clip muta con titolo+gioco: evidenza debole ma ammessa


def test_invented_numbers_flagged():
    r = evaluate(base(overlay_title="Il carrello vola a 300 metri"), ReviewSettings())
    assert "unsupported_numbers" in r.flags


def test_render_invalid_and_clipped():
    r = evaluate(base(render_checks={"ok": False}), ReviewSettings())
    assert "render_invalid" in r.flags
    r = evaluate(base(render_checks={**OK_CHECKS, "text_clipped": True}), ReviewSettings())
    assert "overlay_clipped" in r.flags


def test_duplicate_title():
    r = evaluate(base(recent_titles=["Il carrello ha preso il volo"]), ReviewSettings())
    assert "duplicate_suspect" in r.flags
