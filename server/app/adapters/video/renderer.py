"""VideoRenderer FFmpeg: template → MP4 9:16 verificato. Preview = stesso filtergraph del render."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from PIL import ImageFont

from app.adapters.video import ffmpeg
from app.core.config import get_env
from app.core.errors import PermanentError
from app.domain import captions
from app.services.templates import TemplateConfig

FONT_DIRS = [
    "/usr/share/fonts/truetype/dejavu",
    "/usr/share/fonts/truetype",
    "/usr/share/fonts",
    "C:/Windows/Fonts",
    "/System/Library/Fonts",
    "/Library/Fonts",
]
FALLBACK_FONTS = ["DejaVuSans-Bold.ttf", "arialbd.ttf", "Arial Bold.ttf", "Inter-Bold.otf", "FreeSansBold.ttf"]


def resolve_font(name: str) -> tuple[Path | None, list[str]]:
    """Cerca il font (cartella data/fonts, poi di sistema). Se manca usa un fallback e segnala il warning."""
    warnings: list[str] = []
    p = Path(name)
    if p.is_file():
        return p, warnings
    dirs = [get_env().data_dir / "fonts", get_env().templates_dir / "fonts", *map(Path, FONT_DIRS)]
    for cand in [name, *FALLBACK_FONTS]:
        for d in dirs:
            if not d.exists():
                continue
            hit = d / cand
            if hit.is_file():
                if cand != name:
                    warnings.append(f"font_missing:{name}->fallback:{cand}")
                return hit, warnings
            if cand == name:
                for f in d.rglob(cand):
                    return f, warnings
    for f in Path("/usr/share/fonts").rglob("*.ttf") if Path("/usr/share/fonts").exists() else []:
        warnings.append(f"font_missing:{name}->fallback:{f.name}")
        return f, warnings
    return None, ["font_missing_no_fallback"]


def _hex(c: str) -> str:
    c = c.lstrip("#")
    return "0x" + (c if len(c) in (6, 8) else "FFFFFF")


@dataclass
class TextLayout:
    lines: list[str]
    font_size: int
    width_px: int
    height_px: int
    clipped: bool
    text: str


def layout_text(text: str, cfg: TemplateConfig, font_path: Path | None) -> TextLayout:
    """Wrap multilinea + auto-fit (riduce il font fino a min_font_size). Le emoji vengono rimosse (drawtext non le gestisce)."""
    text = captions.clean_text(captions.strip_emoji(text))
    if cfg.uppercase:
        text = text.upper()
    max_w = int(cfg.canvas_width * cfg.max_width_percent / 100)
    if not text:
        return TextLayout([], cfg.font_size, 0, 0, False, "")
    size = cfg.font_size
    while True:
        font = ImageFont.truetype(str(font_path), size) if font_path else ImageFont.load_default()
        lines: list[str] = []
        cur = ""
        for word in text.replace("\n", " \n ").split(" "):
            if word == "\n":
                lines.append(cur)
                cur = ""
                continue
            trial = (cur + " " + word).strip()
            if font.getlength(trial) <= max_w or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = word
        if cur:
            lines.append(cur)
        widest = max((font.getlength(line_) for line_ in lines), default=0)
        if (len(lines) <= cfg.max_lines and widest <= max_w) or size <= cfg.min_font_size:
            clipped = len(lines) > cfg.max_lines or widest > max_w
            if len(lines) > cfg.max_lines:
                lines = lines[: cfg.max_lines]
                lines[-1] = captions.truncate(lines[-1], max(len(lines[-1]) - 1, 3))
            height = int(len(lines) * size * 1.2)
            return TextLayout(lines, size, int(widest), height, clipped, text)
        size -= 4


@dataclass
class RenderResult:
    path: Path
    checks: dict
    warnings: list[str] = field(default_factory=list)
    fingerprint: str = ""


def fingerprint(source_checksum: str, text: str, cfg: TemplateConfig, extra: dict | None = None) -> str:
    payload = json.dumps({"src": source_checksum, "text": text, "cfg": cfg.model_dump(), "extra": extra or {}}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


def _overlay_window(cfg: TemplateConfig, duration: float) -> tuple[float, float]:
    d = cfg.duration
    if d.mode == "full":
        return 0.0, duration
    if d.mode == "first_seconds":
        return 0.0, min(d.seconds, duration)
    return min(d.start, duration), min(d.end, duration)


def build_filter(cfg: TemplateConfig, src: dict, textfile: Path | None, layout: TextLayout, font_path: Path | None,
                 duration: float) -> tuple[str, list[str]]:
    """Costruisce il filtergraph. Ritorna (filter_complex, warnings)."""
    W, H = cfg.canvas_width, cfg.canvas_height
    warns: list[str] = []
    sw, sh = src["width"], src["height"]
    src_ratio, dst_ratio = sw / sh, W / H
    ox, oy = (cfg.crop_offset_x + 1) / 2, (cfg.crop_offset_y + 1) / 2
    if abs(src_ratio - dst_ratio) < 0.02:
        base = f"[0:v]scale={W}:{H},setsar=1[base]"
    elif src_ratio > dst_ratio:  # landscape → 9:16
        warns.append("landscape_converted")
        if cfg.landscape_mode == "letterbox":
            base = (
                f"[0:v]split[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=30:5[bg];"
                f"[b]scale={W}:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[base]"
            )
        else:
            base = (
                f"[0:v]scale=-2:{H},crop={W}:{H}:x='(iw-{W})*{ox:.3f}':y='(ih-{H})*{oy:.3f}',setsar=1[base]"
            )
            warns.append("crop_unvalidated")
    else:  # più stretto di 9:16 → riempi e taglia sopra/sotto
        base = f"[0:v]scale={W}:-2,crop={W}:{H}:x=0:y='(ih-{H})*{oy:.3f}',setsar=1[base]" if sw / W * sh >= H else \
            f"[0:v]scale=-2:{H},crop={W}:{H}:x='(iw-{W})*{ox:.3f}':y=0,setsar=1[base]"
    if not layout.lines or font_path is None or textfile is None:
        return base.replace("[base]", "[vout]"), warns
    t0, t1 = _overlay_window(cfg, duration)
    fi, fo = cfg.fade_in, cfg.fade_out
    alpha = f"if(lt(t,{t0 + fi:.3f}),(t-{t0:.3f})/{max(fi, 0.001):.3f},if(gt(t,{t1 - fo:.3f}),({t1:.3f}-t)/{max(fo, 0.001):.3f},1))" if (fi or fo) else "1"
    y_center = H * cfg.y_percent / 100 + cfg.vertical_offset
    y_expr = f"{y_center:.1f}-text_h/2"
    x_expr = {"center": "(w-text_w)/2", "left": f"{int(W * (100 - cfg.max_width_percent) / 200)}", "right": f"w-text_w-{int(W * (100 - cfg.max_width_percent) / 200)}"}[cfg.align]
    parts = [
        f"fontfile='{_esc_path(font_path)}'",
        f"textfile='{_esc_path(textfile)}'",
        "expansion=none",  # il titolo è testo letterale: '%{...}' e backslash non devono essere interpretati da drawtext
        f"fontsize={layout.font_size}",
        f"fontcolor={_hex(cfg.text_color)}",
        f"borderw={cfg.stroke_width}",
        f"bordercolor={_hex(cfg.stroke_color)}",
        f"x={x_expr}",
        f"y={y_expr}",
        "line_spacing=8",
        f"text_align={'C' if cfg.align == 'center' else 'L' if cfg.align == 'left' else 'R'}",
        f"alpha='{alpha}'",
        f"enable='between(t,{t0:.3f},{t1:.3f})'",
    ]
    if cfg.shadow:
        parts += [f"shadowcolor={_hex(cfg.shadow_color)}", f"shadowx={cfg.shadow_offset}", f"shadowy={cfg.shadow_offset}"]
    if cfg.background_box:
        parts += ["box=1", f"boxcolor={_hex(cfg.box_color)}@{cfg.box_opacity}", "boxborderw=24"]
    return base + ";[base]drawtext=" + ":".join(parts) + "[vout]", warns


def _esc_path(p: Path) -> str:
    s = p.as_posix()
    return re.sub(r"([:'\\,\[\]])", r"\\\1", s)


class FFmpegRenderer:
    def __init__(self, fps: int = 30, video_bitrate_k: int = 6000, audio_bitrate_k: int = 128, preset: str = "veryfast"):
        self.fps, self.vb, self.ab, self.preset = fps, video_bitrate_k, audio_bitrate_k, preset

    def _prep(self, source: Path, text: str, cfg: TemplateConfig, workdir: Path):
        if not ffmpeg.have_binary("ffmpeg"):
            raise PermanentError("ffmpeg non trovato (PATH, cartelle comuni Windows o CLIPY_FFMPEG_DIR)", code="ffmpeg_missing")
        src = ffmpeg.probe(source)
        font_path, fwarn = resolve_font(cfg.font_file)
        layout = layout_text(text, cfg, font_path)
        textfile = None
        if layout.lines:
            workdir.mkdir(parents=True, exist_ok=True)
            fd, name = _tmp_text(workdir)
            os.write(fd, "\n".join(layout.lines).encode("utf-8"))
            os.close(fd)
            textfile = Path(name)
        flt, warns = build_filter(cfg, src, textfile, layout, font_path, src["duration"])
        return src, layout, textfile, flt, fwarn + warns

    def render(self, source: Path, text: str, cfg: TemplateConfig, out_path: Path, *, source_checksum: str = "") -> RenderResult:
        tmp_dir = get_env().dir("temp")
        src, layout, textfile, flt, warns = self._prep(source, text, cfg, tmp_dir)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_out = out_path.with_suffix(".part.mp4")
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(source), "-filter_complex", flt, "-map", "[vout]"]
        if src["has_audio"]:
            cmd += ["-map", "0:a:0", "-af", "aresample=async=1:first_pts=0", "-c:a", "aac", "-b:a", f"{self.ab}k", "-ar", "44100", "-ac", "2"]
        else:
            warns.append("no_audio")
        if src["height"] * src["width"] < cfg.canvas_width * cfg.canvas_height * 0.25:
            warns.append("low_source_resolution")  # upscaling forte: output comunque 1080×1920
        cmd += ["-r", str(self.fps), "-c:v", "libx264", "-preset", self.preset, "-profile:v", "high", "-pix_fmt", "yuv420p",
                "-b:v", f"{self.vb}k", "-maxrate", f"{int(self.vb * 1.5)}k", "-bufsize", f"{self.vb * 2}k",
                "-movflags", "+faststart", "-f", "mp4", str(tmp_out)]
        try:
            r = ffmpeg.run(cmd, 900)
            if r.returncode != 0:
                raise PermanentError(f"ffmpeg render fallito: {r.stderr.strip()[-400:]}", code="render_failed")
            checks = verify_output(tmp_out, cfg, layout, src)
            if not checks["ok"] and not checks.get("file_ok", False):
                raise PermanentError(f"Output non valido: {checks}", code="render_invalid")
            os.replace(tmp_out, out_path)  # atomico
        finally:
            tmp_out.unlink(missing_ok=True)
            if textfile:
                textfile.unlink(missing_ok=True)
        fp = fingerprint(source_checksum, text, cfg)
        return RenderResult(out_path, checks, warns, fp)

    def preview_still(self, source: Path, text: str, cfg: TemplateConfig, out_png: Path, at_seconds: float | None = None) -> RenderResult:
        """Frame statico con lo stesso filtergraph del render finale."""
        tmp_dir = get_env().dir("temp")
        src, layout, textfile, flt, warns = self._prep(source, text, cfg, tmp_dir)
        t0, t1 = _overlay_window(cfg, src["duration"])
        t = at_seconds if at_seconds is not None else min(max((t0 + t1) / 2, 0.1), max(src["duration"] - 0.1, 0.1))
        out_png.parent.mkdir(parents=True, exist_ok=True)
        tmp = out_png.with_suffix(".part.png")
        try:
            r = ffmpeg.run(["ffmpeg", "-y", "-v", "error", "-i", str(source), "-filter_complex", flt, "-map", "[vout]",
                            "-ss", f"{t:.2f}", "-frames:v", "1", "-f", "image2", str(tmp)], 120)
            # -ss dopo -i: decodifica fino a t (filtri temporali coerenti col render)
            if r.returncode != 0 or not tmp.exists():
                raise PermanentError(f"Preview fallita: {r.stderr.strip()[-300:]}", code="preview_failed")
            os.replace(tmp, out_png)
        finally:
            tmp.unlink(missing_ok=True)
            if textfile:
                textfile.unlink(missing_ok=True)
        return RenderResult(out_png, {"ok": True, "at": t, "lines": layout.lines, "font_size": layout.font_size}, warns)


def _tmp_text(workdir: Path):
    import tempfile

    return tempfile.mkstemp(suffix=".txt", dir=str(workdir))


def verify_output(path: Path, cfg: TemplateConfig, layout: TextLayout, src: dict) -> dict:
    out = ffmpeg.probe(path)
    ratio = out["width"] / out["height"]
    expected = cfg.canvas_width / cfg.canvas_height
    checks = {
        "width": out["width"], "height": out["height"], "duration": out["duration"], "fps": out["fps"],
        "vcodec": out["vcodec"], "acodec": out["acodec"], "pix_fmt": out["pix_fmt"], "has_audio": out["has_audio"],
        "size_bytes": out["size_bytes"],
        "aspect_ok": abs(ratio - expected) < 0.01,
        "codec_ok": out["vcodec"] == "h264" and out["pix_fmt"] == "yuv420p" and (out["acodec"] in (None, "aac")),
        "duration_ok": abs(out["duration"] - src["duration"]) < max(1.0, src["duration"] * 0.1) and out["duration"] >= 1.0,
        "audio_ok": (out["has_audio"] == src["has_audio"]),
        "text_clipped": bool(layout.clipped),
        "safe_area_ok": safe_area_ok(cfg, layout),
        "av_sync_ok": _av_sync_ok(out),
        "min_dim_ok": min(out["width"], out["height"]) >= 360,
        "file_ok": out["size_bytes"] > 1000,
    }
    checks["ok"] = all(checks[k] for k in ("aspect_ok", "codec_ok", "duration_ok", "audio_ok", "min_dim_ok", "file_ok"))
    return checks


# La UI di TikTok copre ~top 8% e ~bottom 20% (+ colonna azioni a destra): stime prudenti, da confermare sul template di riferimento.
SAFE_TOP, SAFE_BOTTOM = 0.08, 0.80


def safe_area_ok(cfg: TemplateConfig, layout: TextLayout) -> bool:
    if not layout.lines:
        return True
    H, W = cfg.canvas_height, cfg.canvas_width
    cy = H * cfg.y_percent / 100 + cfg.vertical_offset
    top, bottom = cy - layout.height_px / 2, cy + layout.height_px / 2
    margin = W * cfg.safe_margin_percent / 100
    return top >= H * SAFE_TOP and bottom <= H * SAFE_BOTTOM and layout.width_px <= W - 2 * margin


def _av_sync_ok(out: dict) -> bool:
    a, v = out.get("audio_duration"), out.get("video_duration")
    return True if not a or not v else abs(a - v) <= 0.5


def remove_tree(p: Path) -> None:
    shutil.rmtree(p, ignore_errors=True)
