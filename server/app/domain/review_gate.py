"""Review gate ibrida e deterministica.

Non si basa sulla sola `confidence` dichiarata dall'LLM: combina segnali osservabili
(evidenze, controlli ffprobe/render, PII, parole vietate, coerenza testo/trascrizione, duplicati, crop).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.core.settings_model import ReviewSettings
from app.domain import captions

POLICY_VERSION = "gate-1.0"

PRESETS = {
    "prudent": {"min_confidence": 0.6, "require_evidence": True},
    "balanced": {"min_confidence": 0.45, "require_evidence": True},
    "permissive": {"min_confidence": 0.3, "require_evidence": False},
}


@dataclass
class GateInput:
    transcript: str = ""
    visual_notes: list = field(default_factory=list)
    game_name: str = ""
    clip_title: str = ""
    overlay_title: str = ""
    caption: str = ""
    hashtags: list[str] = field(default_factory=list)
    confidence: float = 0.0
    llm_requires_review: bool = False
    llm_reason: str | None = None
    quality_flags: list[str] = field(default_factory=list)
    asr_available: bool = True
    render_checks: dict | None = None
    render_warnings: list[str] = field(default_factory=list)
    landscape_fallback: bool = False
    max_caption_length: int = 2200
    banned_words: list[str] = field(default_factory=list)
    max_overlay_chars: int = 60
    max_emoji: int = 1
    recent_titles: list[str] = field(default_factory=list)
    simulated_scenario: str = ""  # solo demo: SAFE/AMBIGUOUS/BLOCKED


@dataclass
class GateResult:
    state: str  # AUTO_READY | NEEDS_REVIEW
    reasons: list[dict]
    flags: list[str]
    policy_version: str = POLICY_VERSION

    @property
    def needs_review(self) -> bool:
        return self.state == "NEEDS_REVIEW"


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[0-9A-Za-zÀ-ÿ]{4,}", text.lower())}


def evaluate(inp: GateInput, cfg: ReviewSettings) -> GateResult:
    reasons: list[dict] = []
    flags: list[str] = []

    def flag(code: str, msg: str) -> None:
        flags.append(code)
        reasons.append({"code": code, "message": msg})

    thr = PRESETS.get(cfg.preset, PRESETS["prudent"])
    min_conf = cfg.min_confidence if cfg.min_confidence is not None else thr["min_confidence"]

    # 1. media / render
    checks = inp.render_checks or {}
    if not checks.get("ok"):
        flag("render_invalid", "Il render finale non ha superato i controlli ffprobe/safe-area.")
    if checks and not checks.get("has_audio", True) and inp.transcript.strip():
        flag("audio_missing", "Manca la traccia audio nonostante una trascrizione.")
    if checks.get("text_clipped"):
        flag("overlay_clipped", "L'overlay non rientra nella safe area (testo tagliato).")
    if "crop_unvalidated" in inp.render_warnings or "crop_subject_risk" in inp.render_warnings:
        flag("crop_warning", "Il crop 16:9→9:16 potrebbe rendere incomprensibile la scena.")
    elif inp.landscape_fallback and cfg.review_landscape_crop:
        flag("landscape_fallback", "Portrait non disponibile: conversione automatica da landscape.")

    # 2. evidenze
    evidence = (
        len(inp.transcript.strip()) >= 15
        or len(inp.visual_notes) > 0
        or bool(inp.clip_title.strip() and inp.game_name.strip())  # metadata Twitch: evidenza debole ma osservabile
    )
    if cfg.require_evidence and not evidence:
        flag("insufficient_evidence", "Nessuna evidenza sufficiente (trascrizione o frame) per sostenere il titolo.")
    if not inp.asr_available and cfg.review_if_asr_unavailable:
        flag("asr_unavailable", "Trascrizione locale non disponibile per questa clip.")

    # 3. segnali dell'analisi
    if inp.confidence < min_conf:
        flag("low_confidence", f"Confidenza dichiarata {inp.confidence:.2f} sotto soglia {min_conf:.2f} (segnale non calibrato).")
    if inp.llm_requires_review:
        flag("llm_requires_review", inp.llm_reason or "L'analisi ha richiesto una revisione umana.")
    for qf in inp.quality_flags:
        if qf in ("sensitive_content", "privacy", "rights_uncertain", "hate_or_harassment"):
            flag(qf, f"Segnale dell'analisi: {qf}.")

    # 4. testo
    ov, cap = inp.overlay_title.strip(), inp.caption.strip()
    if not ov:
        flag("overlay_empty", "Titolo overlay vuoto.")
    if len(ov) > inp.max_overlay_chars:
        flag("overlay_too_long", f"Overlay oltre {inp.max_overlay_chars} caratteri.")
    if len(cap) > inp.max_caption_length:
        flag("caption_too_long", "Caption oltre il limite configurato.")
    if not cap:
        flag("caption_empty", "Caption vuota.")
    if len(inp.hashtags) > 5:
        flag("too_many_hashtags", "Più di 5 hashtag.")
    if captions.count_emoji(ov + cap) > max(inp.max_emoji, 0) + 3:
        flag("emoji_overuse", "Uso eccessivo di emoji.")
    banned = captions.find_banned(ov + " " + cap, inp.banned_words)
    if banned:
        flag("banned_words", "Parole vietate nel testo generato: " + ", ".join(banned))
    if cfg.detect_pii:
        pii = captions.find_pii(inp.transcript + " " + ov + " " + cap)
        if pii:
            flag("pii_detected", "Possibili dati personali rilevati (" + ", ".join(pii) + ").")

    # 5. coerenza titolo/evidenze (anti-invenzione): se esistono evidenze il titolo deve toccarle
    if evidence and ov:
        ev_tokens = _tokens(inp.transcript) | _tokens(" ".join(map(str, inp.visual_notes))) | _tokens(inp.game_name) | _tokens(inp.clip_title)
        numbers_ov = set(re.findall(r"\d+", ov + " " + cap))
        numbers_ev = set(re.findall(r"\d+", inp.transcript + " " + " ".join(map(str, inp.visual_notes)) + " " + inp.clip_title))
        if numbers_ov - numbers_ev:
            flag("unsupported_numbers", "Il testo cita numeri non presenti nelle evidenze.")
        if ev_tokens and not (_tokens(ov + " " + cap) & ev_tokens) and not inp.simulated_scenario:
            flag("title_not_grounded", "Il titolo non ha parole in comune con trascrizione/frame/metadata.")

    # 6. duplicati
    for t in inp.recent_titles:
        if t and captions.title_similarity(ov, t) >= 0.85:
            flag("duplicate_suspect", "Titolo quasi identico a una clip recente.")
            break

    # 7. fixture demo
    if inp.simulated_scenario == "AMBIGUOUS":
        flag("simulated_ambiguous", "[DEMO] Scenario AMBIGUOUS simulato: evidenze deboli.")
    elif inp.simulated_scenario == "BLOCKED":
        flag("simulated_blocked", "[DEMO] Scenario BLOCKED simulato: contenuto sensibile.")

    state = "NEEDS_REVIEW" if flags else "AUTO_READY"
    return GateResult(state=state, reasons=reasons, flags=flags)
