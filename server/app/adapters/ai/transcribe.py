from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

from app.adapters.ai.base import Transcript
from app.adapters.video import ffmpeg
from app.adapters.video.fixtures import FIXTURES
from app.core.config import get_env
from app.core.errors import ClipyError

_model_lock = threading.Lock()
_models: dict[tuple[str, str], object] = {}


# RAM indicativa (GB) per modello faster-whisper con int8 su CPU (stime prudenti, non misurate qui)
MODEL_RAM_GB = {"tiny": 1, "base": 1, "small": 2, "medium": 5, "large-v2": 10, "large-v3": 10, "distil-large-v3": 6}
PY_SUPPORTED = ((3, 9), (3, 13))  # intervallo per cui esistono wheel di ctranslate2/faster-whisper (da verificare su Windows)


def system_memory_gb() -> float | None:
    """RAM totale in GB (Linux/macOS via sysconf, Windows via GlobalMemoryStatusEx). None se non determinabile."""
    try:
        if sys.platform == "win32":
            import ctypes

            class MemStatus(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong), ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]

            st = MemStatus()
            st.dwLength = ctypes.sizeof(MemStatus)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))  # type: ignore[attr-defined]
            return round(st.ullTotalPhys / 1024**3, 1)
        return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024**3, 1)
    except Exception:
        return None


def model_cached(model: str) -> bool:
    """True se il modello è già nella cache locale (data/models): evita download a sorpresa durante l'elaborazione."""
    root = get_env().data_dir / "models"
    if Path(model).is_dir():
        return True
    return root.exists() and any(root.glob(f"*{model}*"))


def asr_health(model: str = "small", device: str = "auto") -> dict:
    try:
        import faster_whisper  # noqa: F401

        installed, err = True, None
    except Exception as exc:
        installed, err = False, f"{type(exc).__name__}: {exc}"[:200]
    cuda = False
    try:
        import ctranslate2

        cuda = ctranslate2.get_cuda_device_count() > 0
    except Exception:
        pass
    ram, need = system_memory_gb(), MODEL_RAM_GB.get(model)
    py = sys.version_info[:2]
    warnings = []
    if not (PY_SUPPORTED[0] <= py <= PY_SUPPORTED[1]):
        warnings.append(f"Python {py[0]}.{py[1]} fuori dall'intervallo testato per faster-whisper ({PY_SUPPORTED[0][0]}.{PY_SUPPORTED[0][1]}–{PY_SUPPORTED[1][0]}.{PY_SUPPORTED[1][1]}): se l'installazione fallisce usa Python 3.12.")
    if ram and need and ram < need * 1.5:
        warnings.append(f"RAM {ram} GB probabilmente insufficiente per il modello '{model}' (~{need} GB): usa un modello più piccolo (base/small).")
    return {"faster_whisper_installed": installed, "import_error": err, "cuda_available": cuda, "model": model, "device": device,
            "model_cached": model_cached(model) if installed else False, "ram_gb": ram, "model_ram_gb": need, "python": f"{py[0]}.{py[1]}", "warnings": warnings}


class FasterWhisperTranscriber:
    """ASR locale. Se faster-whisper, il modello o l'audio non sono utilizzabili restituisce available=False con la causa
    (non inventa mai testo). Cancellazione cooperativa: il loop sui segmenti si interrompe oltre `timeout_s`."""

    def __init__(self, model: str = "small", device: str = "auto", timeout_s: int = 600):
        self.model_name, self.device, self.timeout_s = model, device, timeout_s

    def _load(self):
        key = (self.model_name, self.device)
        with _model_lock:
            if key not in _models:
                from faster_whisper import WhisperModel

                dev = self.device
                if dev == "auto":
                    dev = "cuda" if asr_health(self.model_name)["cuda_available"] else "cpu"
                root = str(get_env().dir("models"))
                try:
                    _models[key] = WhisperModel(self.model_name, device=dev, compute_type="float16" if dev == "cuda" else "int8", download_root=root)
                except Exception:
                    if dev != "cuda":
                        raise
                    # GPU/cuDNN non utilizzabili: fallback automatico su CPU
                    _models[key] = WhisperModel(self.model_name, device="cpu", compute_type="int8", download_root=root)
            return _models[key]

    def transcribe(self, video: Path, language: str | None) -> Transcript:
        def unavailable(note: str) -> Transcript:
            return Transcript("", [], language or "", "faster-whisper", available=False, note=note[:300])

        try:
            info = ffmpeg.probe(video)
        except ClipyError as exc:
            return unavailable(f"audio non analizzabile: {exc.message}")
        if not info["has_audio"]:
            return Transcript("", [], language or "", "faster-whisper", available=True, note="nessuna traccia audio / clip muta")
        wav = get_env().dir("temp") / f"{video.stem}.asr.wav"
        try:
            try:
                extracted = ffmpeg.extract_audio_wav(video, wav)
            except ClipyError as exc:
                return unavailable(f"estrazione audio fallita: {exc.message}")
            if not extracted:
                return unavailable("traccia audio presente ma corrotta/non decodificabile")
            try:
                model = self._load()
            except Exception as exc:
                return unavailable(f"ASR non disponibile (modello non caricabile o non scaricabile): {type(exc).__name__}: {exc}")
            deadline = time.monotonic() + self.timeout_s
            try:
                segs, tinfo = model.transcribe(str(wav), language=language, vad_filter=True, beam_size=1)  # type: ignore[attr-defined]
                seg_list = []
                for s in segs:  # generatore pigro: la decodifica avviene qui
                    seg_list.append({"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()})
                    if time.monotonic() > deadline:
                        return unavailable(f"trascrizione interrotta oltre il timeout di {self.timeout_s}s")
            except Exception as exc:
                return unavailable(f"trascrizione fallita: {type(exc).__name__}: {exc}")
            return Transcript(" ".join(s["text"] for s in seg_list).strip(), seg_list, tinfo.language, "faster-whisper")
        finally:
            wav.unlink(missing_ok=True)


class MockTranscriber:
    """Trascrizione SIMULATA per clip demo (testo preso dalle fixture)."""

    def transcribe(self, video: Path, language: str | None) -> Transcript:
        key = None
        for k in FIXTURES:
            if k in video.name or k in video.parent.name:
                key = k
        text = FIXTURES[key].transcript if key else ""
        return Transcript(text, [{"start": 0.0, "end": 3.0, "text": text}] if text else [], language or "it", "mock-asr", simulated=True,
                          note="trascrizione simulata dalle fixture demo")
