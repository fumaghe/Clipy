# Test report — STEP 2 (22/10/2026, sandbox Linux, Python 3.13.16, Node 22, FFmpeg 6.1.1)

Tutti i risultati sotto sono **reali** (eseguiti con `scripts/run-tests.sh --e2e`, esito `TUTTO OK`). Nessun test è stato rimosso o indebolito per ottenere il verde; i fallimenti emersi durante lo sviluppo sono elencati in fondo.

## Esito complessivo
| Suite | Comando | Esito |
|---|---|---|
| Lint backend | `ruff check app` | OK |
| Backend | `pytest app/tests` | **231 passed** (460 s) — 58 della V1 + 173 nuovi |
| Typecheck | `tsc --noEmit` | OK |
| Lint frontend | `eslint src` | OK |
| Frontend unit | `vitest run` | **9 passed** (5 V1 + 4 nuovi) |
| Build | `vite build` | OK |
| E2E demo | `playwright test` (backend reale in demo) | **13 passed** (11 V1 + 2 nuovi) |

## Classificazione
**1. Test unitari / puri** (nessun I/O esterno): schedule/DST e slot configurabili (`test_schedule`, `test_state_machine`), review gate e motivazioni (`test_gate`, parte di `test_ai_hardening`), transizioni di stato legali, sanificazione dei log, formato dei segreti (backend DPAPI finto), parsing/validazione JSON LLM, vitest.

**2. Integrazione con trasporto simulato** (httpx `MockTransport` / oggetti finti, ffmpeg reale in locale): `test_buffer_adapter` (35), `test_twitch_hardening` (29), `test_publishing` + `test_publishing_hardening` (23), `test_ai_hardening` (LLM e Whisper finti), `test_diagnostics` (18), `test_collector`, `test_pipeline`, `test_renderer*` (FFmpeg reale), `test_api`, `test_security`, `test_windows_scripts` (analisi statica).
> Queste suite verificano **parsing, paginazione, classificazione degli errori e idempotenza**, **non** la compatibilità con i servizi reali. Le fixture Buffer riproducono la forma descritta nel brief di STEP 2 (la documentazione non era raggiungibile).

**3. E2E in demo** (Playwright + backend reale + DB temporaneo, Twitch/AI/publisher simulati): 13 scenari — Overview, Run once→pipeline, Library, Review approva/scarta, anteprima template, coda e dry-run, storico (nessun «Pubblicato» in dry-run), live bloccato senza prerequisiti, wizard Buffer demo, AI Studio, **diagnostica Settings** (simulazione vs stato, test FFmpeg reale, conferma richiesta per lo storage), **difese Origin/segreti**.

**4. Live — NON ESEGUITI** (richiedono account/PC Windows; procedura in `docs/LIVE_TEST_CHECKLIST.md`): Twitch Helix/OAuth reali e download clip; Buffer GraphQL reale (schema, `createPost`); R2 reale; provider LLM reali; `faster-whisper` con modello vero (non installato nel sandbox); avvio su Windows con `start-clipy.bat`/`start-dev.ps1`/`check-windows.ps1` (**mai eseguiti**, nessun PowerShell nel sandbox: solo analisi statica e test sui contenuti); chiamate DPAPI reali. **Nessuna pubblicazione reale è stata effettuata.**

## Copertura per fase (test aggiunti)
| Fase | Regressioni / casi verificati |
|---|---|
| 1 Buffer | organizzazioni multiple; canali TikTok; canali disconnessi/non TikTok; account senza organizzazioni; fallback campi opzionali; paginazione 257/300 elementi (3 pagine) e cursore bloccato ⇒ *incompleto*; enum letterali (`automatic`, `customScheduled`); `dueAt` UTC; escaping/non-iniezione; URL non HTTPS; `MutationError`/tipi sconosciuti; errori HTTP 400/401/403/429(+Retry-After)/5xx, DNS, timeout, JSON illeggibile, `data` nullo, `errors` GraphQL (auth/rate limit); nessuna chiave negli errori; **timeout dopo `createPost` ⇒ ambiguo** e recupero del post esistente via `find_posts`; `get_post` + fallback a scansione; introspezione di sola lettura |
| 2 Twitch | token scaduto→refresh→retry unico; refresh revocato; errori di rete in refresh; `editor_id`=utente autenticato; scope mancante; 403/429/5xx/JSON; clip degli spettatori non filtrate; clip malformate; bisezione finestra piena; download OK/troncato/connessione interrotta/URL scaduto/non-HTTPS/MIME errato; portrait preferito; attesa→fallback landscape con URL rigenerata; file corrotto eliminato e riscaricato; timeout→ripristino; **crash a metà scansione senza duplicati**; clip creata tra due polling; overlap/polling configurabili; OAuth: happy path, state falso/scaduto/riusato, autorizzazione negata, scope mancante, account diverso |
| 3 AI | JSON valido/malformato/incompleto/struttura inattesa ⇒ fallback `analysis_failed`; errori 401/429/5xx/400/timeout classificati e senza chiave; base URL e modello configurabili; budget (taglio frame, rifiuto); prompt con dati non fidati; Whisper: non installato (reale), muto, audio corrotto, file illeggibile, errore a metà, timeout, happy path, pulizia wav; gate: analisi fallita, trascrizione inutilizzabile (3 casi), ASR non disponibile vs spento, termini sensibili, caption malformata/URL, titolo non supportato, video invalido, ogni flag ha una motivazione; **errore provider ⇒ clip in revisione senza bloccare le altre**; errori temporanei ripetuti ⇒ revisione; ffmpeg mancante ⇒ errore duro; rigenerazione senza nuovo download |
| 4 Renderer | testo lungo multilinea Unicode (accenti, «», ñ, ç); testo realmente disegnato dentro la safe area e centrato (diff pixel); caratteri speciali/`%{pts}` letterali; fallback landscape (crop e letterbox); sorgente muta; sincronia A/V; font mancante; safe area violata rilevata; stesso filtergraph per preview e render; **crash a metà render** (nessun parziale, re-render ok); render fallito non corrompe il precedente; timeout ffmpeg ⇒ retry nella pipeline; pulizia dei parziali orfani |
| 5 Scheduler / anti-duplicati | stato riletto sotto lock (nessuna seconda create); 5xx ambigua con post esistente ⇒ adottato; nessuna seconda create finché non ci sono ≥2 ricerche complete e vuote + grazia; ricerca incompleta ⇒ revisione; post con testo diverso allo stesso orario ⇒ revisione; 429 con Retry-After; slot scaduto ricollocato; upload orfano ripreso; stati remoti (programmato/in invio/inviato/errore/incerto/verificato esternamente); verifica manuale solo su job reali inviati; seconda istanza non azzera i lock del leader vivo; migrazione 0002 su DB vecchio; default 4 post/giorno; slot e fuso configurabili; riavvio senza duplicati; DST; transizioni legali su cicli completi |
| 6 Windows | analisi statica (`pause`, percorsi con spazi, niente PATH presunto, niente 0.0.0.0, dipendenze non reinstallate a ogni avvio, controlli richiesti); risoluzione ffmpeg con variabili/cartelle Windows; log su file |
| 7 Diagnostica | stati distinti e simulato separato; test FFmpeg reale; Buffer connesso/fallito/da verificare live senza alcuna mutation; storage con conferma + upload/accesso anonimo/cleanup/rimozione effettiva; LLM con conferma; Whisper non installato; invalidazione al cambio config; nessun effetto su clip/coda; nessun segreto nelle risposte |
| 8 Sicurezza | DPAPI: cifratura, lettura, migrazione senza perdita e reversibile, segreto copiato altrove = non leggibile; nessun segreto/DB tracciato da git; bind localhost; filtro log (incl. formatter di uvicorn); Origin/Host; live disattivo di default e non attivabile via PATCH |

## Bug trovati durante STEP 2 (con causa) — tutti con test di regressione
1. **Buffer: query incompatibili** — `organizations.channels` e `posts` senza `organizationId`, un solo `first: 100` senza cursore ⇒ `count_scheduled`/`find_posts` sbagliati con >100 post (limite di coda remota e riconciliazione inaffidabili).
2. **Doppio `createPost` possibile**: `_process` non rileggeva lo stato sotto lock (due istanze/tick con lista obsoleta). Nuovo test `test_same_job_processed_twice_creates_once`.
3. **Riconciliazione che adottava post altrui**: fallback `or found` ignorava la differenza di testo; e *zero* risultati ⇒ retry immediato (anche con ricerca incompleta).
4. **Lock azzerati da una seconda istanza** all'avvio (`_recover` incondizionato).
5. **Job bloccati in `UPLOADING_ASSET`** dopo un crash: nessun codice li riprendeva.
6. **Slot scaduto**: un retry tardivo avrebbe inviato `dueAt` nel passato.
7. **`editor_id` = broadcaster** anche quando l'utente autenticato era un altro; scope mai verificato; `state` OAuth non eliminato in caso di errore.
8. **File scaricato troncato**: `ffprobe` falliva ⇒ clip `FAILED` per sempre con file residuo; nessun controllo `Content-Length`.
9. **Clip malformata nella risposta Twitch** ⇒ `KeyError` e scansione fallita per tutte.
10. **Errore del provider AI ⇒ clip `FAILED`** (non revisione) e `budget_usd_per_clip` mai applicato; `AnalysisOutput` accettava titolo vuoto; eccezioni su struttura inattesa non gestite.
11. **Whisper**: eccezioni durante la decodifica non catturate; nessun timeout; audio corrotto confuso con clip muta; GPU/cuDNN guasti senza fallback.
12. **`drawtext` con espansione attiva**: un titolo con `%{...}` o backslash veniva interpretato; subprocess ffmpeg senza UTF-8 esplicito (Windows) e senza timeout gestito.
13. **Stato `PUBLISHED` presentato come «Pubblicato»** pur essendo solo la conferma del provider.
14. **`start-clipy.bat` si chiudeva subito** in caso di errore; `pip`/`npm install` a ogni avvio; Python/FFmpeg presunti nel PATH (anche lo stub del Microsoft Store).
15. **Vitest raccoglieva gli spec Playwright** (`e2e/*.spec.ts`) e falliva.
16. Introdotti e corretti nello stesso lavoro (rilevati dai test): filtro log che rompeva il formatter di `uvicorn.access` (visto nell'e2e); troncamento accidentale di `events.py` durante un edit (rilevato da test_security ed e2e).

## Limitazioni note dei test
- Lo schema Buffer testato è quello descritto nel brief, non quello reale; `assets: { videos: [...] }` e i metadati TikTok sono ipotesi.
- Il test del crash del render simula l'interruzione sollevando `KeyboardInterrupt` al posto di `ffmpeg`; non uccide un processo reale.
- La concorrenza è verificata deterministicamente (stato obsoleto) e non con vere istanze parallele.
- I contenuti degli script PowerShell non sono stati interpretati da PowerShell.

---
## Addendum STEP 2.1 — correzioni demo Windows (22/10/2026, sandbox Linux)
Dettagli e diagnosi in `docs/STEP_2_1_WINDOWS_DEMO_FIXES.md`.

| Suite | Esito |
|---|---|
| `ruff check app` | OK |
| `pytest app/tests` | **246 passed** (231 + 15 nuovi in `test_windows_demo_fixes.py`); una asserzione di `test_state_machine.py` aggiornata di proposito: documentava come «non prevista» proprio la transizione `AUTO_READY → DOWNLOADED`, ora dichiarata (rianalisi esplicita) |
| `tsc` / `eslint` / `vite build` | OK |
| `vitest` | **14 passed** (9 + 5 nuovi in `media.test.tsx`) |
| `playwright` (demo, backend reale) | **13 passed** |

Bug emersi dai nuovi test: `HEAD` su `/video` e `/preview` rispondeva 405; la rianalisi da `REJECTED/SKIPPED` era un no-op silenzioso; la modifica testi su una clip già pianificata creava una nuova versione (⇒ secondo post).
**Non verificabile in sandbox:** il `ProactorEventLoop` di Windows (il filtro è testato con record sintetici e il server con RST reali su Linux); script `.bat/.ps1`.
