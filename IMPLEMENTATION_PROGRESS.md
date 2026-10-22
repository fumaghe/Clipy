# Clipy — avanzamento implementazione

Legenda: **[DONE]** implementato e verificato con test · **[PARTIAL]** implementato ma con parti non verificabili senza account · **[BLOCKED]** serve input esterno.
Tutto ciò che richiede account/rete reali è marcato **NON VALIDATO LIVE**. Tipi di test usati nei report: *unitario* · *integrazione con trasporto simulato (MockTransport/finti)* · *E2E demo (Playwright, backend reale in modalità demo)* · *live (mai eseguito)*.

## M0 — Discovery · [DONE]
- Audit ambiente (Python 3.13, Node 22, FFmpeg 6.1 + drawtext, no GPU). Repo di partenza vuoto.
- Documentazione Twitch/Buffer/TikTok **non raggiungibile** dal sandbox (DNS) → contratti `NEEDS_LIVE_VERIFICATION`; riferimento TikTok `REFERENCE_NOT_VERIFIED`. Dettagli: `docs/DECISIONS.md`.

## M1 — Running skeleton · [DONE]
Backend FastAPI + SQLAlchemy/Alembic, settings Pydantic versionate, segreti write-only, adapter Mock*, fixture MP4 generate con FFmpeg, dashboard React collegata a endpoint reali, health, script di avvio, `.env.example`.

## M2 — Twitch · [PARTIAL] — rinforzato in STEP 2
- [DONE] scansione con overlap 24 h, backfill 24 h, paginazione completa (+ bisezione finestre piene), filtro `created_at`, deduplica, nessun filtro per creator, cursore che non avanza su errore, clip malformate saltate, download portrait preferito con attesa configurabile e fallback landscape→9:16, download su `.part` con verifica `Content-Length` + `ffprobe` (file troncato eliminato e riscaricato), nuovo URL ad ogni tentativo.
- [DONE, STEP 2] OAuth: `state` monouso/scadenza, scope verificato prima di salvare il token, identità dell'utente salvata e confrontata col canale, `editor_id` = utente autenticato, rinnovo del token su 401 (rotazione refresh salvata), errori di rete/revoca distinti.
- **NON VALIDATO LIVE**: tutte le chiamate Helix reali, formato esatto delle risposte, risoluzione `iDensTv`→`broadcaster_id`.

## M3 — AI + Review Gate · [PARTIAL] — rinforzato in STEP 2
- [DONE] ASR `faster-whisper` (lazy, CPU/GPU con fallback CPU, timeout cooperativo, audio corrotto/muto/modello non caricabile distinti, mai testo inventato), prompt versionato con dati non fidati, JSON validato (titolo/caption obbligatori, un retry, poi fallback), budget per clip applicato prima della chiamata, timeout configurabile, presets di tono, parole vietate, rigenerazione senza ripetere download/ASR.
- [DONE, STEP 2] Analisi fallita → `NEEDS_REVIEW` con motivazione (non `FAILED`, non blocca la coda); nuove ragioni: `analysis_failed`, `transcript_unusable`, `asr_unavailable` (default ON), `sensitive_terms`, `caption_malformed`, `caption_has_url`, `overlay_outside_safe_area`, `av_desync`.
- **NON VALIDATO LIVE**: provider LLM reali e `faster-whisper` con modello vero.

## M4 — Video · [DONE] — rinforzato in STEP 2
Template JSON, `FFmpegRenderer` (filtergraph unico per render e preview), 1080×1920 H.264/AAC `yuv420p`. STEP 2: `expansion=none` (testo letterale), sincronia A/V (`aresample`) e controlli `safe_area_ok`/`av_sync_ok`, timeout → retry, output atomico (rinomina a successo, render precedente intatto se il nuovo fallisce), pulizia dei parziali orfani. Template ancora `reference_verified=false`.

## M5 — Scheduler · [DONE]
`calculate_next_slots` pura (DST), FIFO, priorità, riordino, pausa, annullo con ripianificazione, tetto giornaliero, numero e orari configurabili. STEP 2: tabella delle transizioni legali verificata da test sull'intero ciclo; slot scaduto ricollocato prima dell'invio.

## M6 — Publishing · [PARTIAL] — riscritto in STEP 2
- [DONE, STEP 2] **Adapter Buffer allineato allo schema descritto nel brief**: `account{organizations}` → `channels(input:{organizationId})` → `posts(input:{organizationId,filter},first,after)` con paginazione cursor completa, `createPost` con enum `automatic`/`customScheduled`, `dueAt` UTC, letterali escapati, `PostActionSuccess`/`MutationError`, errori HTTP/GraphQL/rete classificati, nessun segreto nei messaggi, introspezione dello schema in sola lettura. `organization_id` e `channel_id` salvati insieme; più organizzazioni/canali TikTok gestiti.
- [DONE, STEP 2] Idempotenza: stato riletto sotto lock, esito incerto ⇒ riconciliazione (nessuna seconda create), retry sicuro solo dopo ricerche complete e vuote, ricerca incompleta/ambigua ⇒ revisione; recupero dei job rimasti in `UPLOADING_ASSET`; lock orfani liberati solo dal leader unico.
- [DONE, STEP 2] Stati remoti: `LOCAL`/`SENDING`/`SCHEDULED_ON_BUFFER`/`BUFFER_REPORTS_SENT`/`BUFFER_REPORTS_ERROR`/`EXTERNALLY_VERIFIED`/`UNCERTAIN`; «Buffer segnala inviato» non è «verificato su TikTok» (conferma manuale).
- **NON VALIDATO LIVE**: schema Buffer reale (in particolare `assets` e metadati TikTok), R2 reale, post reale su TikTok. **Nessuna pubblicazione reale è stata eseguita.**

## M7 — UI di prodotto · [DONE]
Overview, Clip Library, Review Inbox, AI Studio, Template Studio, Coda/Calendario, Storico, Settings. STEP 2: **Diagnostica integrazioni** (stati distinti, test con conferma esplicita per ciò che scrive/costa), wizard Buffer con organizzazioni e idoneità canali, badge di stato remoto, bottone «Ho verificato su TikTok».

## M8 — Hardening · [DONE]
Suite backend (pytest), frontend (vitest), e2e Playwright, script di test, documentazione. Esito in `docs/TEST_REPORT.md`.

## STEP 2 — Integration fixes & live readiness · [DONE per ciò che è eseguibile senza account]
| Fase | Esito |
|---|---|
| 1 Buffer GraphQL | [DONE] adapter, parsing, paginazione, errori, test con fixture; **non validato live** |
| 2 Twitch | [DONE] OAuth/refresh/editor_id/download integri/paginazione/ripartenza; **non validato live** |
| 3 AI & Review gate | [DONE] fallback → revisione, budget, ASR robusto, motivazioni esplicite |
| 4 Renderer | [DONE] testo letterale, A/V sync, safe area, crash/timeout, atomicità |
| 5 Scheduler & anti-duplicati | [DONE] vedi M6 |
| 6 Windows | [PARTIAL] script riscritti e analizzati staticamente; **mai eseguiti su Windows** |
| 7 Diagnostica | [DONE] |
| 8 Sicurezza | [DONE] DPAPI (logica testata con backend finto), TrustedHost/Origin, filtro log, nessun segreto tracciato |
| 9 Test | vedi `docs/TEST_REPORT.md` |
| 10 Documentazione | [DONE] incl. `docs/LIVE_TEST_CHECKLIST.md` |

## STEP 2.1 — Correzioni dalla demo su Windows · [DONE in sandbox, da riverificare su Windows]
WinError 10054 (player a richiesta, HEAD, filtro mirato dei reset del client, file aperti su Windows) · preview 404 (placeholder per stato, `media_not_ready`) · reprocessing esplicito/atomico/senza duplicati (`AUTO_READY → DOWNLOADED`) · 409 `busy` previsto con comandi disattivati in UI. Dettagli: `docs/STEP_2_1_WINDOWS_DEMO_FIXES.md`. DRY RUN invariato, nessun dato eliminato.

## Prossime attività (richiedono te) — dettaglio in `docs/LIVE_TEST_CHECKLIST.md`
1. Primo avvio su Windows con `start-clipy.bat` e verifica degli script.
2. Registrare l'app Twitch, autorizzare `iDensTv`, importare clip reali.
3. `faster-whisper` + chiave LLM, primo video reale.
4. Buffer (API key, *Verifica schema*) + R2; **un** post supervisionato prima di abilitare il live.
5. Importare screenshot/video del TikTok di riferimento e confermare il template.
