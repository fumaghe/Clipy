from __future__ import annotations

import threading
from pathlib import Path

from app.adapters.ai.base import Transcript
from app.adapters.video import ffmpeg
from app.adapters.video.fixtures import FIXTURES
from app.core.config import get_env

_model_lock = threading.Lock()
_models: dict[tuple[str, str], object] = {}


def asr_health(model: str = "small", device: str = "auto") -> dict:
    try:
        import faster_whisper  # noqa: F401

        installed = True
    except Exception:
        installed = False
    cuda = False
    try:
        import ctranslate2

        cuda = ctranslate2.get_cuda_device_count() > 0
    except Exception:
        pass
    return {"faster_whisper_installed": installed, "cuda_available": cuda, "model": model, "device": device}


class FasterWhisperTranscriber:
    """ASR locale. Se faster-whisper o il modello non sono disponibili restituisce available=False (non inventa testo)."""

    def __init__(self, model: str = "small", device: str = "auto"):
        self.model_name, self.device = model, device

    def _load(self):
        key = (self.model_name, self.device)
        with _model_lock:
            if key not in _models:
                from faster_whisper import WhisperModel

                dev = self.device
                compute = "int8"
                if dev == "auto":
                    h = asr_health()
                    dev = "cuda" if h["cuda_available"] else "cpu"
                if dev == "cuda":
                    compute = "float16"
                _models[key] = WhisperModel(self.model_name, device=dev, compute_type=compute,
                                            download_root=str(get_env().dir("models")))
            return _models[key]

    def transcribe(self, video: Path, language: str | None) -> Transcript:
        wav = get_env().dir("temp") / f"{video.stem}.asr.wav"
        try:
            if not ffmpeg.extract_audio_wav(video, wav):
                return Transcript("", [], language or "", "faster-whisper", available=True, note="nessuna traccia audio / clip muta")
            try:
                model = self._load()
            except Exception as exc:
                return Transcript("", [], language or "", "faster-whisper", available=False, note=f"ASR non disponibile: {exc}"[:300])
            segs, info = model.transcribe(str(wav), language=language, vad_filter=True, beam_size=1)  # type: ignore[attr-defined]
            seg_list = [{"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()} for s in segs]
            return Transcript(" ".join(s["text"] for s in seg_list).strip(), seg_list, info.language, "faster-whisper")
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
