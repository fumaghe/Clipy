"""Pulizia di file temporanei/parziali lasciati da crash o interruzioni (render, download, ASR, frame)."""
from __future__ import annotations

import time
from pathlib import Path

from app.core.config import get_env

STALE_SECONDS = 3600  # un file parziale più vecchio di 1 ora non può appartenere a un lavoro in corso


def cleanup_stale_files(max_age_seconds: int = STALE_SECONDS) -> int:
    """Elimina `*.part`, `*.part.mp4`, testi temporanei e frame orfani. Non tocca originali/render completi."""
    env = get_env()
    removed = 0
    now = time.time()
    patterns = [("originals", "*.part"), ("rendered", "*.part.mp4"), ("previews", "*.part.png"), ("temp", "*")]
    for sub, pat in patterns:
        base = env.data_dir / sub
        if not base.exists():
            continue
        for p in base.glob(pat):
            try:
                if now - p.stat().st_mtime < max_age_seconds:
                    continue
                if p.is_dir():
                    import shutil

                    shutil.rmtree(p, ignore_errors=True)
                elif p.is_file():
                    p.unlink()
                else:
                    continue
                removed += 1
            except OSError:
                continue
    return removed


def stale_paths(root: Path) -> list[Path]:
    return [p for p in root.glob("*.part*")]
