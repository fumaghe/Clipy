"""Sanitizzazione di titolo overlay, caption e hashtag."""
from __future__ import annotations

import re
import unicodedata

_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF\U0001F900-\U0001F9FF\U0000FE0F\U0000200D]"
)
_HASHTAG_RE = re.compile(r"[^0-9A-Za-zÀ-ÿ_]")
PII_PATTERNS = {
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    "phone": re.compile(r"(?<!\d)(?:\+?\d{2}[\s.-]?)?\d{3}[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)"),
    "iban": re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"),
}


def count_emoji(text: str) -> int:
    return len(_EMOJI_RE.findall(text))


def strip_emoji(text: str) -> str:
    return _EMOJI_RE.sub("", text)


def limit_emoji(text: str, max_emoji: int) -> str:
    seen = 0

    def repl(m: re.Match) -> str:
        nonlocal seen
        seen += 1
        return m.group(0) if seen <= max_emoji else ""

    return _EMOJI_RE.sub(repl, text)


def clean_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text or "")
    text = "".join(ch for ch in text if ch == "\n" or unicodedata.category(ch)[0] != "C")
    return re.sub(r"[ \t]+", " ", text).strip()


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0]
    return (cut or text[: limit - 1]).rstrip(" ,.;:-") + "…"


def normalize_hashtags(tags: list[str], max_tags: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for t in tags:
        core = _HASHTAG_RE.sub("", str(t).lstrip("#"))
        if not core or core.lower() in seen:
            continue
        seen.add(core.lower())
        out.append("#" + core)
        if len(out) >= max_tags:
            break
    return out


def compose_post_text(caption: str, hashtags: list[str], max_len: int) -> str:
    tags = " ".join(hashtags)
    base = caption.strip()
    # i tag vengono rimossi dal corpo se già presenti
    for h in hashtags:
        base = re.sub(re.escape(h) + r"\b", "", base, flags=re.I)
    base = re.sub(r"\s{2,}", " ", base).strip()
    room = max_len - (len(tags) + 2 if tags else 0)
    base = truncate(base, max(room, 10))
    return f"{base}\n\n{tags}".strip()


def find_pii(text: str) -> list[str]:
    return [k for k, rx in PII_PATTERNS.items() if rx.search(text or "")]


def find_banned(text: str, banned: list[str]) -> list[str]:
    low = (text or "").lower()
    return [w for w in banned if w and re.search(r"\b" + re.escape(w.lower()) + r"\b", low)]


def title_similarity(a: str, b: str) -> float:
    ta, tb = set(re.findall(r"\w+", a.lower())), set(re.findall(r"\w+", b.lower()))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)
