# Clipy — avanzamento implementazione

Legenda: **[DONE]** implementato e verificato con test · **[PARTIAL]** implementato ma con parti non verificabili senza account · **[BLOCKED]** serve input esterno.
Tutto ciò che richiede account/rete reali è marcato **NON VALIDATO LIVE**.

## M0 — Discovery · [DONE]
- Audit ambiente (Python 3.13, Node 22, FFmpeg 6.1 + drawtext, no GPU). Repo di partenza vuoto.
- Documentazione Twitch/Buffer/TikTok **non raggiungibile** dal sandbox (DNS) → contratti `NEEDS_LIVE_VERIFICATION`; riferimento TikTok `REFERENCE_NOT_VERIFIED`. Dettagli: `docs/DECISIONS.md`.
- `ARCHITECTURE.md`, `docs/DECISIONS.md`, `docs/TEMPLATE_REFERENCE.md`.

## M1 — Running skeleton · [DONE]
Backend FastAPI + SQLAlchemy/Alembic (13 tabelle, WAL), settings Pydantic versionate, segreti write-only, adapter Mock*, fixture MP4 generate con FFmpeg
(portrait, landscape, muta, rumorosa, ambigua, bloccata), dashboard React collegata a endpoint reali, health, script di avvio Windows/Unix, `.env.example`.
Pulsanti: Start/Stop collector, Run once, Importa clip, Simula clip (solo demo), Render preview, Dry-run publish («Invia ora»).

## M2 — Twitch · [PARTIAL]
- [DONE] (con mock/MockTransport) scansione con overlap 24 h, backfill 24 h al primo avvio, paginazione completa, filtro `created_at`, split finestre, upsert idempotente, nessun filtro per creator,
  cursore che non avanza su errore, download portrait preferito con attesa configurabile e fallback landscape, stream+limite+rename atomico+ffprobe+sha256, download in batch da 10.
- [DONE] OAuth locale con `state`, refresh token.
- **NON VALIDATO LIVE**: tutte le chiamate Helix reali, scope, formato esatto delle risposte, risoluzione `iDensTv`→`broadcaster_id`.

## M3 — AI + Review Gate · [PARTIAL]
- [DONE] ASR `faster-whisper` (lazy, CPU/GPU, mai testo inventato se non disponibile), campionamento frame con dedupe, prompt versionato con dati non fidati, JSON Pydantic validato (retry + fallback → review),
  presets di tono, parole vietate, limiti emoji/hashtag/lunghezza, rigenerazione senza ripetere download/ASR, alternative in AI Studio, stima costi.
- [DONE] Review gate `gate-1.0` deterministica con motivi, Inbox con Approva/Modifica/Rigenera/Scarta/Riprova render, audit in `reviews`.
- **NON VALIDATO LIVE**: provider LLM reali (Anthropic/OpenAI) e `faster-whisper` con modello vero (nel sandbox non installato/scaricabile): coperti solo da mock e trasporto simulato.

## M4 — Video · [DONE]
Template JSON validato e versionato, `FFmpegRenderer` con crop/letterbox, wrap + auto-fit, font con fallback, durata full/primi N s/intervallo, fade, ombra, box; preview = stesso filtergraph; MP4 H.264 yuv420p AAC 1080×1920;
verifica ffprobe post-export; output atomico; skip se fingerprint invariato; Template Studio con anteprima reale e import riferimento. Template ancora `reference_verified=false`.

## M5 — Scheduler · [DONE]
`calculate_next_slots` pura (DST autunno/primavera), FIFO con priorità/riordino/pausa/annullo con ripianificazione, tetto giornaliero, caso 12 clip → 4+4+4, review non consuma slot, lease/leader unico, ripartenza sicura.

## M6 — Publishing · [PARTIAL]
- [DONE] Pipeline publisher con `DRY_RUN_OK` (zero richieste esterne), MockPublisher con ciclo di vita, upload opaco, verifica pubblica del media, persistenza dell'intenzione prima di `createPost`,
  recovery dopo timeout/crash (adozione / retry sicuro / `NEEDS_REVIEW` se ambiguo), backoff esponenziale + jitter, errori permanenti/temporanei, limite remoto (Buffer Free 10), `PUBLISHED` solo da stato remoto, cleanup asset.
- [DONE] Wizard Buffer, interruttore live con frase di conferma e prerequisiti, DRY RUN di default.
- **NON VALIDATO LIVE**: schema GraphQL Buffer reale, R2 reale, post reale su TikTok. **Nessuna pubblicazione reale è stata eseguita.**

## M7 — UI di prodotto · [DONE]
Overview, Clip Library, Review Inbox, AI Studio, Template Studio, Coda/Calendario, Storico, Settings con stati loading/empty/error/success; ogni pulsante chiama un endpoint reale (verificato dall'e2e per i flussi principali).
Non implementato: drag-and-drop nella coda (sostituito da pulsanti su/giù, più robusti), Telegram (opzionale), SSE (polling).

## M8 — Hardening · [DONE]
Suite backend (pytest), frontend (vitest), e2e Playwright, script di test, README/INTEGRATIONS/OPERATIONS/TEST_REPORT. Esito dettagliato in `docs/TEST_REPORT.md`.

## Prossime attività (richiedono te)
1. Registrare l'app Twitch e autorizzare `iDensTv` → validare M2 live.
2. Inserire una chiave LLM e installare `faster-whisper` → validare M3 live.
3. Importare screenshot/video del TikTok di riferimento e confermare il template.
4. Collegare Buffer + R2 e fare un post di prova **supervisionato** prima di abilitare il live.
