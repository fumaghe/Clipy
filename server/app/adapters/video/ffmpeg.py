"""Wrapper sottili su ffmpeg/ffprobe."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from app.core.errors import PermanentError, TemporaryError


def _windows_candidates(name: str) -> list[Path]:
    exe = f"{name}.exe"
    roots = [os.environ.get("LOCALAPPDATA", ""), os.environ.get("ProgramFiles", ""), os.environ.get("ProgramData", ""), "C:\\"]
    out = [Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "WinGet" / "Links" / exe] if os.environ.get("LOCALAPPDATA") else []
    for r in roots:
        if r:
            out += [Path(r) / "ffmpeg" / "bin" / exe, Path(r) / "Programs" / "ffmpeg" / "bin" / exe]
    choco = os.environ.get("ChocolateyInstall")
    if choco:
        out.append(Path(choco) / "bin" / exe)
    return out


def have_binary(name: str) -> str | None:
    """Percorso del binario: variabile CLIPY_<NAME> / CLIPY_FFMPEG_DIR, poi PATH, poi cartelle Windows comuni (winget, choco, C:\\ffmpeg)."""
    explicit = os.environ.get(f"CLIPY_{name.upper()}")
    if explicit and Path(explicit).is_file():
        return explicit
    d = os.environ.get("CLIPY_FFMPEG_DIR")
    if d:
        for cand in (Path(d) / name, Path(d) / f"{name}.exe"):
            if cand.is_file():
                return str(cand)
    found = shutil.which(name)
    if found:
        return found
    if sys.platform == "win32":
        for cand in _windows_candidates(name):
            if cand.is_file():
                return str(cand)
    return None


def run(cmd: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
    """Esegue ffmpeg/ffprobe (risolti con have_binary). UTF-8 esplicito (Windows usa cp1252), niente finestra console, timeout → errore ritentabile."""
    if cmd and cmd[0] in ("ffmpeg", "ffprobe"):
        cmd = [have_binary(cmd[0]) or cmd[0], *cmd[1:]]
    kw: dict = {}
    if sys.platform == "win32":
        kw["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, stdin=subprocess.DEVNULL, **kw)
    except subprocess.TimeoutExpired as exc:
        raise TemporaryError(f"{Path(cmd[0]).stem} oltre il timeout di {timeout}s (processo terminato)", code="ffmpeg_timeout") from exc
    except FileNotFoundError as exc:
        raise PermanentError(f"{Path(cmd[0]).stem} non trovato: installalo o imposta CLIPY_FFMPEG_DIR", code="ffmpeg_missing") from exc


def probe(path: Path) -> dict:
    if not have_binary("ffprobe"):
        raise PermanentError("ffprobe non trovato (PATH, cartelle comuni Windows o CLIPY_FFMPEG_DIR)", code="ffprobe_missing")
    r = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)], 60)
    if r.returncode != 0:
        raise PermanentError(f"ffprobe fallito: {r.stderr.strip()[:300]}", code="ffprobe_failed")
    data = json.loads(r.stdout or "{}")
    v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    a = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    if v is None:
        raise PermanentError("Nessuna traccia video", code="no_video_stream")
    fr = v.get("avg_frame_rate", "0/1")
    try:
        n, d = fr.split("/")
        fps = float(n) / float(d) if float(d) else 0.0
    except Exception:
        fps = 0.0
    duration = float(data.get("format", {}).get("duration") or v.get("duration") or 0)
    return {
        "width": int(v.get("width", 0)),
        "height": int(v.get("height", 0)),
        "fps": round(fps, 2),
        "duration": duration,
        "vcodec": v.get("codec_name"),
        "pix_fmt": v.get("pix_fmt"),
        "has_audio": a is not None,
        "audio_duration": float(a.get("duration") or 0) if a else None,
        "video_duration": float(v.get("duration") or 0) or None,
        "acodec": a.get("codec_name") if a else None,
        "audio_bitrate": int(a.get("bit_rate", 0)) if a and a.get("bit_rate") else None,
        "bit_rate": int(data.get("format", {}).get("bit_rate") or 0),
        "size_bytes": int(data.get("format", {}).get("size") or 0),
        "format": data.get("format", {}).get("format_name"),
    }


def is_portrait(info: dict) -> bool:
    return info["height"] > info["width"]


def extract_audio_wav(video: Path, out: Path) -> bool:
    """True se è stato estratto audio utilizzabile. Solleva TemporaryError solo per timeout (gestito dal chiamante)."""
    r = run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", "-f", "wav", str(out)], 300)
    return r.returncode == 0 and out.exists() and out.stat().st_size > 1000


def extract_frames(video: Path, out_dir: Path, every: float, max_frames: int, duration: float) -> list[Path]:
    """Campiona frame ogni `every` secondi (tetto `max_frames`) e scarta quasi-duplicati."""
    from PIL import Image

    out_dir.mkdir(parents=True, exist_ok=True)
    n = max(1, min(max_frames, int(duration // every) + 1))
    step = max(duration / n, 0.5) if duration else every
    paths: list[Path] = []
    sigs: list[list[int]] = []
    for i in range(n):
        t = min(step * i + step / 2, max(duration - 0.1, 0))
        p = out_dir / f"f{i:02d}.jpg"
        r = run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1", "-vf", "scale=512:-2", str(p)], 60)
        if r.returncode != 0 or not p.exists():
            continue
        with Image.open(p) as im:
            sig = list(im.convert("L").resize((8, 8)).tobytes())
        avg = sum(sig) / len(sig)
        bits = [1 if x > avg else 0 for x in sig]
        if any(sum(a != b for a, b in zip(bits, s, strict=True)) <= 3 for s in sigs):
            p.unlink(missing_ok=True)
            continue
        sigs.append(bits)
        paths.append(p)
    return paths


def make_fixture(path: Path, *, width: int, height: int, seconds: float, audio: bool, label: str, freq: int = 440) -> None:
    """Genera un MP4 di test reale con ffmpeg (lavfi)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=size={width}x{height}:rate=30:duration={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}:sample_rate=44100"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p"]
    if audio:
        cmd += ["-c:a", "aac", "-b:a", "96k", "-shortest"]
    cmd += ["-metadata", f"title={label}", str(path)]
    r = run(cmd, 120)
    if r.returncode != 0:
        raise PermanentError(f"Generazione fixture fallita: {r.stderr[:300]}", code="fixture_failed")
