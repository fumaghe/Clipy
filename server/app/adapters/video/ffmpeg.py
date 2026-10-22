"""Wrapper sottili su ffmpeg/ffprobe."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from app.core.errors import PermanentError


def have_binary(name: str) -> str | None:
    return shutil.which(name)


def run(cmd: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def probe(path: Path) -> dict:
    if not have_binary("ffprobe"):
        raise PermanentError("ffprobe non trovato nel PATH", code="ffprobe_missing")
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
        "acodec": a.get("codec_name") if a else None,
        "audio_bitrate": int(a.get("bit_rate", 0)) if a and a.get("bit_rate") else None,
        "bit_rate": int(data.get("format", {}).get("bit_rate") or 0),
        "size_bytes": int(data.get("format", {}).get("size") or 0),
        "format": data.get("format", {}).get("format_name"),
    }


def is_portrait(info: dict) -> bool:
    return info["height"] > info["width"]


def extract_audio_wav(video: Path, out: Path) -> bool:
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
