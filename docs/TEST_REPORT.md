# Test report (22/10/2026, ambiente di sviluppo Linux, FFmpeg 6.1.1, demo mode)

## Eseguiti — VERDI
| Suite | Comando | Esito |
|---|---|---|
| Lint backend | `ruff check app` | OK |
| Backend | `pytest app/tests` | **58 passed** (256 s): schedule/DST, review gate, collector (dedupe, 24h, creator, cursore, portrait/fallback, Helix MockTransport), pipeline (12 clip→4/4/4, 8 OK + 4 dubbie, clip muta, edit/regenerate, restart render, dry-run zero chiamate, lifecycle mock), renderer (portrait/landscape, titolo lungo+emoji, font mancante, testo vuoto, durata full/primi N s/intervallo con frame diversi), publishing (Buffer MockTransport, crash dopo invio, ambiguità, media non raggiungibile, limite remoto, storage non pubblico, annullo+ripianificazione), API (segreti, flag protetti, live, errori, retention) |
| Typecheck | `tsc --noEmit` | OK |
| Lint frontend | `eslint src` | OK |
| Frontend unit | `vitest run` | 5 passed |
| Build | `vite build` | OK |
| E2E | `playwright test` (backend reale, demo) | **11 passed**: Overview, Run once→pipeline, Library, Review approva/scarta, anteprima template, coda, storico, live bloccato, wizard Buffer demo, AI Studio |

## NON eseguiti / NON VALIDATI LIVE
- Twitch Helix reale, OAuth, download clip reali; Buffer GraphQL reale; R2 reale; post TikTok reale (**nessuna pubblicazione reale eseguita**).
- LLM reali (Anthropic/OpenAI) e `faster-whisper` con modello vero (non installato nel sandbox).
- Avvio con `start-dev.ps1`/`.bat` su Windows (script scritti ma non eseguiti su Windows); `start-dev.sh` non eseguito end-to-end (l'e2e usa `scripts/e2e-server.sh`).
- Coda con drag-and-drop (non implementato); Docker Compose (non incluso).

## Validazione live (passi)
1. Twitch: seguire `docs/INTEGRATIONS.md` §1, «Testa connessione», «Importa ora», verificare clip di uno spettatore.
2. LLM + ASR: §2–3, analizzare una clip reale e controllare transcript/titolo.
3. Buffer/R2: §4–5, «Testa» canali e storage; correggere eventuali differenze di schema in `adapters/publisher/buffer.py`.
4. Test supervisionato con un video di prova prima di abilitare il live.
