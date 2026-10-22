import hashlib
from pathlib import Path

import pytest

from app.adapters.video import ffmpeg
from app.adapters.video.renderer import FFmpegRenderer, layout_text, resolve_font
from app.services.templates import OverlayDuration, TemplateConfig


@pytest.fixture()
def clips(env, tmp_path):
    d = tmp_path / "f"
    ffmpeg.make_fixture(d / "l.mp4", width=1280, height=720, seconds=4, audio=True, label="l")
    ffmpeg.make_fixture(d / "p.mp4", width=720, height=1280, seconds=4, audio=False, label="p")
    return d


def test_landscape_and_portrait_render_valid(clips, tmp_path):
    r = FFmpegRenderer()
    for n, audio in (("l", True), ("p", False)):
        res = r.render(clips / f"{n}.mp4", "Titolo", TemplateConfig(), tmp_path / f"{n}.mp4")
        assert res.checks["ok"] and res.checks["has_audio"] == audio
        assert (res.checks["width"], res.checks["height"]) == (1080, 1920)


def test_long_title_and_emoji_no_clipping(clips, tmp_path):
    long = "Questo è un titolo davvero molto molto lungo con emoji 😂🔥 che deve andare a capo senza uscire dallo schermo mai più"
    cfg = TemplateConfig()
    font, _ = resolve_font(cfg.font_file)
    lay = layout_text(long, cfg, font)
    assert len(lay.lines) <= cfg.max_lines and "😂" not in lay.text
    res = FFmpegRenderer().render(clips / "p.mp4", long, cfg, tmp_path / "o.mp4")
    assert res.checks["ok"]


def test_missing_font_falls_back_with_warning(clips, tmp_path):
    cfg = TemplateConfig(font_file="NonEsisteAffatto.ttf")
    res = FFmpegRenderer().render(clips / "p.mp4", "Ciao", cfg, tmp_path / "o.mp4")
    assert res.checks["ok"] and any(w.startswith("font_missing") for w in res.warnings)


def test_empty_text_renders_without_overlay(clips, tmp_path):
    res = FFmpegRenderer().render(clips / "l.mp4", "", TemplateConfig(), tmp_path / "o.mp4")
    assert res.checks["ok"]


def _frame_hash(r, src, cfg, t, tmp_path, name):
    out = tmp_path / name
    r.preview_still(src, "Titolo di prova", cfg, out, at_seconds=t)
    return hashlib.md5(out.read_bytes()).hexdigest()


def test_overlay_duration_modes_change_real_frames(clips, tmp_path):
    r = FFmpegRenderer()
    src = clips / "p.mp4"
    no_text = TemplateConfig(duration=OverlayDuration(mode="time_range", start=0.1, end=0.2))
    full = TemplateConfig(duration=OverlayDuration(mode="full"))
    first = TemplateConfig(duration=OverlayDuration(mode="first_seconds", seconds=1.0))
    rng = TemplateConfig(duration=OverlayDuration(mode="time_range", start=2.0, end=3.5))
    base_late = _frame_hash(r, src, no_text, 2.5, tmp_path, "a.png")  # nessun testo a t=2.5
    assert _frame_hash(r, src, full, 2.5, tmp_path, "b.png") != base_late  # full: testo presente
    assert _frame_hash(r, src, first, 2.5, tmp_path, "c.png") == base_late  # first_seconds=1: assente a 2.5
    assert _frame_hash(r, src, first, 0.6, tmp_path, "d.png") != _frame_hash(r, src, no_text, 0.6, tmp_path, "e.png")
    assert _frame_hash(r, src, rng, 2.5, tmp_path, "f.png") != base_late  # range 2–3.5: presente a 2.5
    assert _frame_hash(r, src, rng, 0.8, tmp_path, "g.png") == _frame_hash(r, src, no_text, 0.8, tmp_path, "h.png")


def test_preview_uses_same_engine_as_render(clips, tmp_path):
    from app.adapters.video import renderer

    assert FFmpegRenderer.preview_still.__qualname__.startswith("FFmpegRenderer") and "_prep" in FFmpegRenderer.preview_still.__code__.co_names
    assert "_prep" in FFmpegRenderer.render.__code__.co_names
    _ = (renderer, Path)
