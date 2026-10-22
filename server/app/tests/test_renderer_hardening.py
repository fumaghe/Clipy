"""Renderer FFmpeg (Fase 4): testo lungo/Unicode/speciali, fallback landscape, crash/interruzione, atomicità, sync A/V."""
import subprocess
from pathlib import Path

import pytest
from PIL import Image, ImageChops

from app.adapters.video import ffmpeg
from app.adapters.video.renderer import FFmpegRenderer, layout_text, resolve_font, safe_area_ok
from app.core.errors import PermanentError, TemporaryError
from app.services.templates import TemplateConfig

LONG = "Çà va è già così: l'élan — «fortissimo» ñandú, 100% pazzesco, naïve café Zürich, #1 al mondo (davvero) senza fine ancora più lungo di così"
SPECIAL = "50% %{pts} \\n \\\\ 'apici' \"doppi\" a:b,c;d [x] $HOME `ls`"


@pytest.fixture()
def src(env, tmp_path):
    d = tmp_path / "f"
    ffmpeg.make_fixture(d / "land.mp4", width=1280, height=720, seconds=4, audio=True, label="l")
    ffmpeg.make_fixture(d / "port.mp4", width=720, height=1280, seconds=4, audio=True, label="p")
    ffmpeg.make_fixture(d / "mute.mp4", width=720, height=1280, seconds=3, audio=False, label="m")
    return d


R = FFmpegRenderer(preset="ultrafast")


def test_long_multiline_unicode_text_fits_and_renders(src, tmp_path):
    cfg = TemplateConfig()
    font, _ = resolve_font(cfg.font_file)
    lay = layout_text(LONG, cfg, font)
    assert 1 < len(lay.lines) <= cfg.max_lines and lay.width_px <= cfg.canvas_width * cfg.max_width_percent / 100
    assert "è" in lay.text and "Ç" in lay.text  # accenti preservati
    res = R.render(src / "port.mp4", LONG, cfg, tmp_path / "o.mp4")
    c = res.checks
    assert c["ok"] and c["safe_area_ok"] and c["av_sync_ok"] and (c["width"], c["height"]) == (1080, 1920)
    assert c["vcodec"] == "h264" and c["acodec"] == "aac" and c["pix_fmt"] == "yuv420p"


def test_text_is_actually_drawn_inside_the_safe_area(src, tmp_path):
    cfg = TemplateConfig()
    R.preview_still(src / "port.mp4", LONG, cfg, tmp_path / "with.png", at_seconds=1.5)
    R.preview_still(src / "port.mp4", "", cfg, tmp_path / "without.png", at_seconds=1.5)
    a, b = Image.open(tmp_path / "with.png").convert("RGB"), Image.open(tmp_path / "without.png").convert("RGB")
    box = ImageChops.difference(a, b).getbbox()
    assert box, "il testo non è stato disegnato"
    left, top, right, bottom = box
    assert top >= 1920 * 0.08 and bottom <= 1920 * 0.80 and left >= 1080 * 0.05 and right <= 1080 * 0.95
    assert abs(((top + bottom) / 2) - 960) < 200  # testo centrale


def test_special_characters_are_literal_not_interpreted(src, tmp_path):
    cfg = TemplateConfig()
    res = R.render(src / "port.mp4", SPECIAL, cfg, tmp_path / "o.mp4")
    assert res.checks["ok"]
    # '%{pts}' viene reso letteralmente: il filtergraph disattiva l'espansione di drawtext
    from app.adapters.video.renderer import build_filter

    flt, _ = build_filter(cfg, ffmpeg.probe(src / "port.mp4"), Path("/tmp/x.txt"), layout_text("x", cfg, resolve_font(cfg.font_file)[0]), resolve_font(cfg.font_file)[0], 3)
    assert "expansion=none" in flt


def test_landscape_fallback_converts_to_portrait_with_warnings(src, tmp_path):
    res = R.render(src / "land.mp4", "Titolo", TemplateConfig(), tmp_path / "o.mp4")
    assert res.checks["ok"] and (res.checks["width"], res.checks["height"]) == (1080, 1920)
    assert "landscape_converted" in res.warnings and "crop_unvalidated" in res.warnings
    res2 = R.render(src / "land.mp4", "Titolo", TemplateConfig(landscape_mode="letterbox"), tmp_path / "o2.mp4")
    assert res2.checks["ok"] and "landscape_converted" in res2.warnings and "crop_unvalidated" not in res2.warnings


def test_original_portrait_not_flagged_as_converted_and_mute_source_stays_mute(src, tmp_path):
    res = R.render(src / "port.mp4", "Ciao", TemplateConfig(), tmp_path / "o.mp4")
    assert "landscape_converted" not in res.warnings and res.checks["has_audio"]
    m = R.render(src / "mute.mp4", "Ciao", TemplateConfig(), tmp_path / "m.mp4")
    assert m.checks["ok"] and not m.checks["has_audio"] and "no_audio" in m.warnings and m.checks["audio_ok"]


def test_audio_video_sync_and_codec(src, tmp_path):
    out = tmp_path / "o.mp4"
    R.render(src / "land.mp4", "Sync", TemplateConfig(), out)
    info = ffmpeg.probe(out)
    assert info["audio_duration"] and abs(info["audio_duration"] - info["duration"]) < 0.3 and info["fps"] == 30


def test_font_missing_still_renders(src, tmp_path):
    res = R.render(src / "port.mp4", "Ciao", TemplateConfig(font_file="Inesistente.ttf"), tmp_path / "o.mp4")
    assert res.checks["ok"] and any(w.startswith("font_missing") for w in res.warnings)


def test_layout_outside_safe_area_is_detected():
    cfg = TemplateConfig(y_percent=92)
    lay = layout_text("Titolo basso", cfg, resolve_font(cfg.font_file)[0])
    assert not safe_area_ok(cfg, lay)  # coperto dall'interfaccia TikTok
    assert safe_area_ok(TemplateConfig(), layout_text("Titolo", TemplateConfig(), resolve_font(TemplateConfig().font_file)[0]))


def test_preview_and_final_render_share_the_same_filtergraph(src):
    cfg = TemplateConfig()
    _, _, tf1, flt1, _ = R._prep(src / "land.mp4", LONG, cfg, Path(cfg and src / "w1"))
    _, _, tf2, flt2, _ = R._prep(src / "land.mp4", LONG, cfg, Path(src / "w2"))
    norm = lambda f, t: f.replace(str(t.as_posix()).replace(":", "\\:"), "TEXTFILE")  # noqa: E731
    assert norm(flt1, tf1) == norm(flt2, tf2)
    import inspect

    from app.adapters.video import renderer

    assert "build_filter(" in inspect.getsource(renderer.FFmpegRenderer._prep) and "def preview_still" in inspect.getsource(renderer)  # unico punto di costruzione


# ---------------------------------------------------------------- crash / interruzione / atomicità
def test_crash_during_render_leaves_no_partial_output_and_retry_succeeds(src, tmp_path, monkeypatch):
    out = tmp_path / "final.mp4"
    real_run = ffmpeg.run

    def dying(cmd, timeout=600):
        part = Path(cmd[-1])
        if part.name.endswith(".part.mp4"):
            part.write_bytes(b"\x00" * 4096)  # scrittura parziale poi 'kill' del processo
            raise KeyboardInterrupt("processo interrotto")
        return real_run(cmd, timeout)

    monkeypatch.setattr(ffmpeg, "run", dying)
    with pytest.raises(KeyboardInterrupt):
        R.render(src / "port.mp4", "Titolo", TemplateConfig(), out)
    assert not out.exists() and not list(tmp_path.glob("*.part*"))  # niente file parziale spacciabile per valido
    assert not list((Path(out).parent).glob("*.txt"))
    monkeypatch.setattr(ffmpeg, "run", real_run)
    res = R.render(src / "port.mp4", "Titolo", TemplateConfig(), out)
    assert out.exists() and res.checks["ok"]


def test_existing_valid_output_is_not_corrupted_by_a_failed_rerender(src, tmp_path, monkeypatch):
    out = tmp_path / "final.mp4"
    R.render(src / "port.mp4", "Uno", TemplateConfig(), out)
    before = out.read_bytes()
    real_run = ffmpeg.run
    monkeypatch.setattr(ffmpeg, "run", lambda cmd, timeout=600: subprocess.CompletedProcess(cmd, 1, "", "boom") if "-filter_complex" in cmd else real_run(cmd, timeout))
    with pytest.raises(PermanentError):
        R.render(src / "port.mp4", "Due", TemplateConfig(), out)
    assert out.read_bytes() == before  # il vecchio render resta integro (rename atomico solo a successo)


def test_ffmpeg_timeout_is_temporary_and_cleans_up(src, tmp_path, monkeypatch):
    def slow(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(subprocess, "run", slow)
    with pytest.raises(TemporaryError) as e:
        ffmpeg.run(["ffmpeg", "-version"], 1)
    assert e.value.code == "ffmpeg_timeout"


def test_pipeline_retries_after_render_timeout_instead_of_failing(env, db, monkeypatch):
    from sqlalchemy import select

    from app.adapters.twitch.mock import MockTwitch
    from app.db.models import Clip
    from app.domain import states as S
    from app.services import collector, factory, pipeline

    from .conftest import UniqueAnalyzer, set_settings

    factory.OVERRIDES["analyzer"] = UniqueAnalyzer()
    set_settings({"twitch": {"portrait_wait_minutes": 0}})
    MockTwitch().path.write_text("[]")
    MockTwitch().spawn("funny_portrait", minutes_ago=30)
    collector.scan(db)
    calls = {"n": 0}
    real = FFmpegRenderer.render

    def flaky(self, *a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TemporaryError("ffmpeg oltre il timeout", code="ffmpeg_timeout")
        return real(self, *a, **k)

    monkeypatch.setattr(FFmpegRenderer, "render", flaky)
    for _ in range(8):
        pipeline.advance_one(db)
    c = db.scalar(select(Clip))
    assert c.status == S.ANALYZED and c.next_attempt_at and "timeout" in c.error  # in attesa del backoff, non FAILED
    c.next_attempt_at = None
    db.commit()
    pipeline.run_until_idle(db, 10)
    db.refresh(c)
    assert c.status in (S.AUTO_READY, S.NEEDS_REVIEW) and c.render_path and Path(c.render_path).exists()


def test_stale_partial_files_are_cleaned_by_housekeeping(env):
    import os
    import time

    from app.services import housekeeping

    d = env.data_dir
    old, fresh = d / "rendered" / "a.part.mp4", d / "rendered" / "b.part.mp4"
    stale_dl = d / "originals" / "x.mp4.part"
    for p in (old, fresh, stale_dl):
        p.write_bytes(b"x")
    past = time.time() - 7200
    for p in (old, stale_dl):
        os.utime(p, (past, past))
    final = d / "rendered" / "ok.mp4"
    final.write_bytes(b"x")
    os.utime(final, (past, past))
    assert housekeeping.cleanup_stale_files() == 2
    assert not old.exists() and not stale_dl.exists() and fresh.exists() and final.exists()  # in corso e render completi intatti
