"""Fixture demo (generate realmente con ffmpeg). Tutto ciò che deriva da qui è marcato come SIMULATO."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.adapters.video import ffmpeg
from app.core.config import get_env


@dataclass(frozen=True)
class Fixture:
    key: str
    title: str
    game: str
    creator: str
    width: int
    height: int
    seconds: float
    audio: bool
    scenario: str  # SAFE | AMBIGUOUS | BLOCKED
    transcript: str
    visual: tuple[str, ...]
    overlay: str
    caption: str
    hashtags: tuple[str, ...]
    portrait_on_twitch: bool = True
    freq: int = 440


FIXTURES: dict[str, Fixture] = {f.key: f for f in [
    Fixture("funny_portrait", "Il carrello che vola", "GTA V", "viewer_marco", 720, 1280, 6, True, "SAFE",
            "No ma ragazzi hai visto? Il carrello è decollato, ho preso il volo con la macchina!",
            ("Auto che salta da una rampa",), "Il carrello ha preso il volo", "Quando la fisica del gioco decide di non collaborare.",
            ("#gaming", "#gta", "#fail"), True, 440),
    Fixture("skill_landscape", "Ace in 1v4 con la Vandal", "Valorant", "iDensTv", 1280, 720, 7, True, "SAFE",
            "Uno, due, tre, quattro! Ace clutch, non ci credo, la Vandal non perdona!",
            ("Mirino su nemico in sparatoria",), "1v4 con la Vandal: clutch ace", "Quando la mira va da sola e chiude il round da solo contro quattro.",
            ("#valorant", "#clutch", "#gaming"), False, 523),
    Fixture("silent_gameplay", "Salto impossibile", "Elden Ring", "viewer_giulia", 720, 1280, 5, False, "SAFE",
            "", ("Personaggio che cade da una scogliera",), "Salto di fede (spoiler: è andata male)", "Un salto di fede in Elden Ring che finisce esattamente come pensi.",
            ("#eldenring", "#fail", "#gaming"), True),
    Fixture("noisy_reaction", "Jumpscare in cuffia", "Phasmophobia", "iDensTv", 720, 1280, 5, True, "SAFE",
            "Aaah! No no no no! Che cavolo era quello?! Esco da qui!",
            ("Corridoio buio",), "Il jumpscare che ha spaccato le cuffie", "Reazione sincera al jumpscare: le cuffie non hanno retto.",
            ("#horror", "#reaction", "#gaming"), True, 880),
    Fixture("ambiguous_unclear", "boh", "Just Chatting", "viewer_anon", 1280, 720, 5, True, "AMBIGUOUS",
            "mm... allora... cioè... boh", (), "Momento", "Boh, guardate.", ("#gaming",), False, 330),
    Fixture("blocked_personal", "Dati in chat", "Just Chatting", "viewer_x", 720, 1280, 5, True, "BLOCKED",
            "Allora scrivi pure a mario.rossi@example.com oppure chiamami al 333 123 4567",
            ("Schermata con dati personali",), "Mario Rossi ha fatto un casino", "Contatti in chiaro in diretta.", ("#gaming",), True, 300),
]}


def fixture_path(key: str) -> Path:
    return get_env().dir("fixtures") / f"{key}.mp4"


def ensure_fixture(key: str) -> Path:
    fx = FIXTURES[key]
    p = fixture_path(key)
    if not p.exists() or p.stat().st_size < 1000:
        ffmpeg.make_fixture(p, width=fx.width, height=fx.height, seconds=fx.seconds, audio=fx.audio, label=f"clipy-demo:{key}", freq=fx.freq)
    return p
