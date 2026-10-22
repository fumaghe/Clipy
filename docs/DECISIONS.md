# Decisioni tecniche e note M0

## M0 — audit ambiente (22/10/2026)
Python 3.13, Node 22, npm 10, **FFmpeg/ffprobe 6.1.1 con `drawtext`**, Linux 4 core / 15 GB, nessuna GPU CUDA (ASR su CPU), Chromium Playwright preinstallato.
Repository di partenza: solo `README.md` e il master prompt.

## M0 — fattibilità API: documentazione NON raggiungibile
Dal sandbox di sviluppo la risoluzione DNS verso `dev.twitch.tv`, `developers.buffer.com`, `support.buffer.com` e `vm.tiktok.com` è bloccata (verificato con curl e WebFetch: `ENOTFOUND`).
**Quindi nessuna delle pagine ufficiali elencate nel master prompt è stata letta.** Conseguenze, tutte esplicite:

| Area | Cosa è implementato | Stato |
|---|---|---|
| Twitch Get Clips (`/helix/clips`, `first=100`, `after`, `started_at/ended_at`) | paginazione completa, filtro su `created_at`, split finestre > 7 giorni (scelta prudenziale) | `NEEDS_LIVE_VERIFICATION` |
| Twitch Get Clips Download (`/helix/clips/downloads`, ≤10 ID, `portrait_download_url`/`landscape_download_url`) | come da master prompt; `broadcaster_id` + `editor_id` = broadcaster | `NEEDS_LIVE_VERIFICATION` |
| OAuth Twitch (`channel:manage:clips`) | authorization-code con `state` anti-CSRF (scadenza 10 min), refresh automatico | `NEEDS_LIVE_VERIFICATION` |
| Buffer GraphQL (`https://api.buffer.com`, `createPost`, `PostActionSuccess`/`MutationError`, query post/canali) | query isolate come costanti in `adapters/publisher/buffer.py`; nomi dei campi di query/filtri **ipotizzati** | `NEEDS_LIVE_VERIFICATION` (alta probabilità di ritocchi) |
| Limiti TikTok/Buffer (3 s–10 min, ≤1 GB, ≥360 px, 23–60 fps, ≤5 hashtag, 25 post/24 h) | applicati come default configurabili (fps 30, hashtag ≤5, audio ≤128 kbps, video 6 Mbps) | da ricontrollare |
| Video TikTok di riferimento | non accessibile | `REFERENCE_NOT_VERIFIED` (`docs/TEMPLATE_REFERENCE.md`) |

Il test con MockTransport verifica il parsing e la gestione errori, **non** la compatibilità con lo schema reale. Prima del live: `Settings → Buffer → Testa` e un post di prova supervisionato.

## Scelte di prodotto
- **Nessun limite hardcoded** a 4 post/giorno: `daily_max` e `slots` sono impostazioni validate; default 4 con slot 10:00/13:30/17:30/21:00 `Europe/Rome`.
- `max_caption_length` default 2200 (configurabile): non si impone il limite arbitrario di 150.
- **DRY_RUN reale**: con `provider=buffer` e live non abilitato i job diventano `DRY_RUN_OK` senza upload né `createPost`. `provider=mock` simula l'intero ciclo (marcato SIMULATO).
- Abilitazione live: solo `POST /settings/live/enable` con frase esatta, Buffer collegato, canale selezionato e storage S3 pubblico; `PATCH /settings` rifiuta `live_enabled`/`is_connected`.
- Clip muta: non scartata. Evidenza = trascrizione ≥15 caratteri **oppure** note visive **oppure** (titolo Twitch + gioco). Senza nulla → review.
- Analisi simulata (nessun provider) su clip **reale** → sempre `NEEDS_REVIEW` (`simulated_analysis`), per non spacciare per analisi vera un fallback.
- Emoji nell'overlay: `drawtext` non rende emoji a colori → vengono rimosse dal testo renderizzato (restano nella caption). Comportamento prevedibile e testato.
- Una clip con job in qualsiasi stato per la versione di contenuto corrente non ne ottiene un secondo (vincolo `(clip, canale, versione)`); riattivazione solo esplicita («Aggiungi alla coda», «Riprova»).
- Migrazione Alembic `0001` crea lo schema v1 dai modelli; le successive devono essere incrementali.
- Segreti in chiaro nel DB SQLite locale (utente singolo, bind localhost). In cloud: usare un secret manager.
- **UI**: Tailwind + componenti propri invece di shadcn/ui (la CLI di shadcn richiede rete/generazione di file; i componenti in `components/ui.tsx` coprono Button/Card/Badge/Modal/Toast con stessa estetica). Nessun grafico decorativo.
- Docker Compose: non incluso (opzionale nel master prompt); l'avvio locale Windows/Linux è coperto dagli script.


# STEP 2 — decisioni (23/10/2026)

## Documentazione ancora non raggiungibile
Verificato di nuovo (curl e WebFetch → `ENOTFOUND developers.buffer.com`). Gli adapter sono stati allineati alle strutture indicate nel brief di STEP 2 e documentati come **NON VALIDATI LIVE**. Per ridurre il rischio al primo collaudo: introspezione dello schema Buffer in sola lettura (*Diagnostica → Buffer*), varianti di riserva dei campi opzionali, scansione paginata se `post(input:{id})` non combacia.

## Buffer
- Valori dinamici come **letterali GraphQL escapati** invece di variabili tipizzate: evita di indovinare i nomi dei tipi di input; gli enum (`automatic`, `customScheduled`, `scheduled`) sono veri enum. Test di non-iniezione.
- `organization_id` salvato insieme a `channel_id`; se mancante viene risolto cercando il canale tra le organizzazioni.
- Il contenuto di `assets` (`{ videos: [{ url }] }`) e i metadati TikTok sono ipotesi da confermare al primo post supervisionato.
- Errori marcati `ambiguous` sulle mutation (vedi ARCHITECTURE): un timeout dopo `createPost` non causa mai un secondo invio cieco.

## Pubblicazione
- `PUBLISHED` = «Buffer segnala inviato». Nuovo campo `verification`; `EXTERNALLY_VERIFIED` solo con conferma manuale (`POST /publications/{id}/verify-external`). Etichetta UI cambiata da «Pubblicato» a «Inviato (Buffer)».
- Retry sicuro dopo esito incerto solo con ≥ 2 ricerche complete e vuote + 5 minuti di grazia (eventual consistency di Buffer). Scelta prudente: può ritardare di qualche minuto, mai duplicare.
- Il `_recover` all'avvio non azzera i lock se un altro leader è vivo (bug V1: una seconda istanza liberava i lock della prima → doppio invio possibile).

## Twitch
- `editor_id` = utente proprietario del token (da `/oauth2/validate`), non più sempre `broadcaster_id`.
- Callback OAuth: `state` monouso eliminato anche in caso di errore; token salvato solo se lo scope richiesto è concesso; identità salvata in `Setting("twitch_auth")`.
- File scaricato troncato = errore ritentabile (file eliminato, nuovo URL), non `FAILED` definitivo.

## AI / review gate
- Analisi AI fallita (chiave, budget, 5xx persistenti, JSON non valido) → **fallback descrittivo + `NEEDS_REVIEW`** invece di `FAILED`: la clip non si perde e non blocca la coda. Solo `ffmpeg/ffprobe` mancanti restano errore duro (problema di ambiente).
- `review_if_asr_unavailable` ora **True** di default: ASR guasto/non installato ≠ ASR spento esplicitamente (`transcription=off`).
- Budget per clip applicato con stima *prima* della chiamata (`0` = illimitato). Stima volutamente prudente: due tentativi al massimo di output.
- Termini sensibili: elenco di radici configurabile (`review.sensitive_terms`), euristica grossolana → revisione, non blocco.

## Render
- `drawtext` con `expansion=none` (titoli con `%{…}` o backslash sono testo letterale). `aresample=async=1` per la sincronia A/V. Controlli aggiunti: `safe_area_ok` (fascia alta 8% / bassa 20%, stime prudenti da confermare sul riferimento TikTok), `av_sync_ok`. Il design del template non è cambiato.
- Timeout ffmpeg = `TemporaryError` (retry con backoff) invece di fallimento definitivo.

## Windows
- `start-clipy.bat` non si chiude più da solo; Python/FFmpeg/Node individuati senza presumere il PATH (`py -3.x`, winget, Chocolatey, `C:\ffmpeg\bin`, `CLIPY_FFMPEG_DIR`); `pip`/`npm install` e build frontend solo se cambiano lockfile/sorgenti; log su `data/logs/clipy.log`; subprocess con UTF-8 e senza finestre console.
- **Mai eseguiti su Windows**: analisi statica + test di regressione sui contenuti degli script.

## Sicurezza
- Segreti cifrati a riposo con DPAPI su Windows (ctypes, nessuna dipendenza), migrazione trasparente e reversibile (`python -m app.tools.secrets_cli`). Alternative scartate: keyring (dipendenza extra, stessa garanzia utente-locale), cifratura con chiave nel DB (inutile).
- Middleware `TrustedHost` (anti DNS-rebinding) e controllo `Origin` sulle richieste che modificano stato (anti CSRF cross-site), più filtro di sanificazione su tutti i log (inclusi gli access log che contengono `?code=` dell'OAuth).
- Vitest ora include solo `src/**/*.test.*` (prima raccoglieva anche gli spec Playwright).

## Diagnostica
Stati: `NOT_CONFIGURED` · `CONFIGURED` · `CONNECTED` · `TEST_FAILED` · `NEEDS_LIVE_VERIFICATION`; `simulated` separato. Un risultato vale solo per la configurazione testata (impronta di impostazioni e segreti). I test che scrivono o costano richiedono `confirm=true`. Buffer "connesso" = lettura + schema compatibile (la pubblicazione resta da verificare con il post supervisionato).
