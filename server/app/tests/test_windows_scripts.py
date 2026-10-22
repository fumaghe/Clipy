"""Analisi statica degli script Windows (NON eseguibili in questo sandbox Linux): regressioni su problemi noti."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def read(p: str) -> str:
    return (ROOT / p).read_text("utf-8")


def test_bat_never_closes_on_error_and_checks_powershell():
    bat = read("start-clipy.bat")
    assert "pause" in bat.lower()  # bug V1: la finestra si chiudeva subito in caso di errore
    assert 'cd /d "%~dp0"' in bat and '"%~dp0scripts\\start-dev.ps1"' in bat  # percorsi con spazi
    assert "where powershell" in bat and "-NoProfile" in bat


def test_start_script_does_not_assume_path_and_handles_spaces():
    ps = read("scripts/start-dev.ps1")
    assert "Find-Python" in ps and "Find-Ffmpeg" in ps and "CLIPY_FFMPEG_DIR" in ps
    assert ".venv\\Scripts\\python.exe" in ps and "-LiteralPath" in ps
    assert "catch" in ps and "exit 1" in ps  # errori mostrati, non finestra muta
    assert "--host\", \"127.0.0.1\"" in ps or "--host 127.0.0.1" in ps  # solo localhost
    assert "0.0.0.0" not in ps
    assert "/health" in ps  # il browser si apre solo quando il backend risponde
    assert "-Prod" in ps and "npm run build" in ps


def test_deps_not_reinstalled_on_every_start():
    ps = read("scripts/start-dev.ps1")
    assert "Get-FileSha" in ps and ".clipy-requirements.sha" in ps  # bug V1: pip install a ogni avvio


def test_check_script_covers_required_checks():
    chk = read("scripts/check-windows.ps1")
    for needle in ("Find-Python", "Find-Ffmpeg", "drawtext", "node", "npm", "Test-PortFree", "Free", "faster_whisper"):
        assert needle in chk, needle
    common = read("scripts/clipy-common.ps1")
    assert "WindowsApps" in common  # scarta lo stub del Microsoft Store
    assert "WinGet" in common and "ChocolateyInstall" in common


def test_ffmpeg_resolution_supports_windows_locations_and_overrides(tmp_path, monkeypatch):
    from app.adapters.video import ffmpeg

    fake = tmp_path / "ffmpeg"
    fake.write_text("x")
    monkeypatch.setenv("CLIPY_FFMPEG", str(fake))
    assert ffmpeg.have_binary("ffmpeg") == str(fake)
    monkeypatch.delenv("CLIPY_FFMPEG")
    d = tmp_path / "bin"
    d.mkdir()
    (d / "ffprobe.exe").write_text("x")
    monkeypatch.setenv("CLIPY_FFMPEG_DIR", str(d))
    assert ffmpeg.have_binary("ffprobe") == str(d / "ffprobe.exe")


def test_file_logging_survives_missing_console(env):
    import logging

    from app.main import setup_file_logging

    setup_file_logging()
    logging.getLogger("clipy").warning("riga di prova")
    root = logging.getLogger()
    for h in list(root.handlers):
        h.flush()
    try:
        assert "riga di prova" in (env.data_dir / "logs" / "clipy.log").read_text("utf-8")
    finally:
        from logging.handlers import RotatingFileHandler

        for h in list(root.handlers):
            if isinstance(h, RotatingFileHandler):
                root.removeHandler(h)
                h.close()
