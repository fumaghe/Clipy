from __future__ import annotations

import base64
import json
import re
import time

import httpx

from app.adapters.ai.base import AnalysisContext, AnalysisOutput, AnalysisResult
from app.adapters.ai.prompts import PROMPT_VERSION, build_system, build_user
from app.adapters.video.fixtures import FIXTURES
from app.core.errors import PermanentError, TemporaryError
from app.domain import captions

PRICE_PER_MTOK = {"in": 3.0, "out": 15.0}  # STIMA indicativa, non listino ufficiale


def _estimate(tin: int, tout: int) -> float:
    return round(tin / 1e6 * PRICE_PER_MTOK["in"] + tout / 1e6 * PRICE_PER_MTOK["out"], 5)


def parse_json_output(text: str) -> AnalysisOutput:
    """Estrae e valida il JSON. Rifiuta output vuoti/privi dei campi essenziali (non vengono 'riempiti' inventando)."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise ValueError("nessun JSON nella risposta")
    out = AnalysisOutput.model_validate(json.loads(m.group(0)))
    if not out.overlay_title.strip():
        raise ValueError("overlay_title mancante")
    if not out.tiktok_caption.strip():
        raise ValueError("tiktok_caption mancante")
    return out


def fallback_result(ctx: AnalysisContext, reason: str, provider: str = "fallback", model: str = "", timings: dict | None = None,
                    tokens_in: int = 0, tokens_out: int = 0) -> AnalysisResult:
    """Risultato descrittivo costruito SOLO dal titolo Twitch, marcato `analysis_failed` → la clip finisce in NEEDS_REVIEW."""
    base = ctx.clip_title or "Clip Twitch"
    out = AnalysisOutput(language=ctx.language, scene_summary=base, overlay_title=base, tiktok_caption=base, hashtags=["#gaming"], confidence=0.1,
                         requires_review=True, reason=reason, quality_flags=["analysis_failed"])
    return AnalysisResult(postprocess(out, ctx), provider, model, PROMPT_VERSION, False, tokens_in, tokens_out, _estimate(tokens_in, tokens_out), timings or {})


def estimate_input_tokens(system: str, user: str, n_images: int) -> int:
    return int((len(system) + len(user)) / 3.5) + n_images * 800  # stima prudente (immagini ~800 token ciascuna)


def postprocess(out: AnalysisOutput, ctx: AnalysisContext) -> AnalysisOutput:
    """Applica vincoli deterministici: lunghezze, emoji, hashtag, overlay ≠ caption."""
    out.overlay_title = captions.truncate(captions.limit_emoji(captions.clean_text(out.overlay_title), ctx.max_emoji), ctx.max_overlay_chars)
    cap = captions.truncate(captions.clean_text(out.tiktok_caption), ctx.max_caption_chars)
    if cap.strip().lower() == out.overlay_title.strip().lower():
        cap = captions.truncate(f"{out.scene_summary or ctx.clip_title}".strip() or cap, ctx.max_caption_chars)
    out.tiktok_caption = cap
    out.hashtags = captions.normalize_hashtags(out.hashtags, ctx.max_hashtags)
    return out


class MockAnalyzer:
    """Analisi SIMULATA: per clip demo usa i testi delle fixture; per altre clip genera un fallback descrittivo
    dai metadata (non fingendo di aver 'visto' il video)."""

    def analyze(self, ctx: AnalysisContext) -> AnalysisResult:
        t0 = time.time()
        fx = FIXTURES.get(ctx.demo_fixture or "")
        if fx:
            variants = [fx.overlay, f"{fx.overlay} (di nuovo)", f"POV: {fx.title}", f"{fx.game}: {fx.title}"]
            title = variants[ctx.variation % len(variants)]
            conf = {"SAFE": 0.86, "AMBIGUOUS": 0.35, "BLOCKED": 0.7}[fx.scenario]
            flags = ["privacy"] if fx.scenario == "BLOCKED" else []
            out = AnalysisOutput(language="it", scene_summary=fx.caption, overlay_title=title, tiktok_caption=fx.caption,
                                 hashtags=list(fx.hashtags), content_category="funny", confidence=conf,
                                 requires_review=fx.scenario != "SAFE", reason=None if fx.scenario == "SAFE" else "[DEMO] scenario simulato",
                                 quality_flags=flags)
        else:
            base = ctx.clip_title or "Clip Twitch"
            spoken = ctx.transcript.text.strip()
            title = f"{base}" if not spoken else captions.truncate(spoken.split(".")[0], ctx.max_overlay_chars)
            out = AnalysisOutput(language=ctx.language, scene_summary=f"{base} ({ctx.game_name})".strip(), overlay_title=title,
                                 tiktok_caption=f"{base} — {ctx.game_name}".strip(" —"), hashtags=["#gaming", "#twitch"],
                                 content_category="other", confidence=0.5 if (spoken or ctx.game_name) else 0.2,
                                 requires_review=False, reason=None)
        out = postprocess(out, ctx)
        return AnalysisResult(out, "mock", "mock-analyzer", PROMPT_VERSION, simulated=True, timings={"total_s": round(time.time() - t0, 3)})


class LLMAnalyzer:
    MAX_OUTPUT_TOKENS = 800

    def __init__(self, provider: str, api_key: str, model: str, base_url: str = "", vision: bool = False,
                 http: httpx.Client | None = None, timeout: int = 60):
        self.provider, self.key, self.model, self.base_url, self.vision = provider, api_key, model, base_url.rstrip("/"), vision
        self.http = http or httpx.Client(timeout=httpx.Timeout(timeout, connect=15))
        if not api_key:
            raise PermanentError("Chiave LLM mancante: impostala in AI Studio / Settings", code="llm_key_missing")
        if not model:
            raise PermanentError("Modello LLM non configurato", code="llm_model_missing")

    def _call(self, system: str, user: str, images: list[bytes]) -> tuple[str, int, int]:
        try:
            if self.provider == "anthropic":
                content: list[dict] = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(i).decode()}} for i in images]
                content.append({"type": "text", "text": user})
                r = self.http.post(self.base_url or "https://api.anthropic.com/v1/messages", headers={
                    "x-api-key": self.key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                    json={"model": self.model, "max_tokens": self.MAX_OUTPUT_TOKENS, "system": system, "messages": [{"role": "user", "content": content}]})
                self._raise(r)
                j = self._body(r)
                return "".join(b.get("text", "") for b in j["content"] if isinstance(b, dict)), j["usage"]["input_tokens"], j["usage"]["output_tokens"]
            content2: list[dict] = [{"type": "text", "text": user}] + [
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(i).decode()}} for i in images]
            url = (self.base_url or "https://api.openai.com/v1") + "/chat/completions"
            r = self.http.post(url, headers={"Authorization": f"Bearer {self.key}"}, json={
                "model": self.model, "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": content2}]})
            self._raise(r)
            j = self._body(r)
            u = j.get("usage", {})
            return j["choices"][0]["message"]["content"], u.get("prompt_tokens", 0), u.get("completion_tokens", 0)
        except httpx.HTTPError as exc:
            raise TemporaryError(f"LLM non raggiungibile: {exc}", code="llm_unreachable") from exc

    @staticmethod
    def _body(r: httpx.Response) -> dict:
        try:
            j = r.json()
        except ValueError as exc:
            raise ValueError("risposta del provider non è JSON") from exc
        if not isinstance(j, dict):
            raise ValueError("risposta del provider inattesa")
        return j

    def _scrub(self, text: str) -> str:
        return text.replace(self.key, "***")[:200]

    def _raise(self, r: httpx.Response) -> None:
        if r.status_code in (401, 403):
            raise PermanentError("Chiave LLM rifiutata o senza permessi", code="llm_auth")
        if r.status_code == 429:
            ra = r.headers.get("retry-after", "")
            raise TemporaryError("LLM: rate limit", code="llm_rate_limited", retry_after=float(ra) if ra.replace(".", "", 1).isdigit() else None)
        if r.status_code >= 500:
            raise TemporaryError(f"LLM {r.status_code}", code="llm_temporary")
        if r.status_code >= 400:
            raise PermanentError(f"LLM {r.status_code}: {self._scrub(r.text)}", code="llm_error")

    def analyze(self, ctx: AnalysisContext) -> AnalysisResult:
        t0 = time.time()
        system, user = build_system(ctx), build_user(ctx)
        images = [p.read_bytes() for p in ctx.frames] if self.vision else []
        dropped = False
        if ctx.budget_usd > 0:
            # limite di costo: stima PRIMA della chiamata (2 tentativi max). Prima si rinuncia alle immagini, poi si rifiuta.
            def est(n_img: int) -> float:
                return 2 * _estimate(estimate_input_tokens(system, user, n_img), self.MAX_OUTPUT_TOKENS)

            if est(len(images)) > ctx.budget_usd and images:
                images, dropped = [], True
            if est(len(images)) > ctx.budget_usd:
                raise PermanentError(f"Costo stimato ${est(len(images)):.3f} oltre il budget ${ctx.budget_usd:.3f} per clip: analisi non eseguita", code="llm_budget")
        tin = tout = 0
        out: AnalysisOutput | None = None
        err = ""
        for attempt in range(2):
            try:
                text, a, b = self._call(system, user if attempt == 0 else user + f"\nRisposta precedente non valida ({err}). Restituisci SOLO JSON valido.", images)
                tin, tout = tin + a, tout + b
                out = parse_json_output(text)
                break
            except (ValueError, KeyError, TypeError, IndexError) as exc:  # JSON malformato, campi mancanti, struttura inattesa
                err = self._scrub(str(exc))[:120]
        if out is None:  # output non valido dopo il retry → descrittivo + revisione umana (mai testo inventato)
            return fallback_result(ctx, f"Output LLM non valido ({err})", self.provider, self.model, {"total_s": round(time.time() - t0, 2)}, tin, tout)
        out = postprocess(out, ctx)
        if dropped:
            out.quality_flags = [*out.quality_flags, "vision_skipped_budget"]
        return AnalysisResult(out, self.provider, self.model, PROMPT_VERSION, False, tin, tout, _estimate(tin, tout), {"total_s": round(time.time() - t0, 2)})
