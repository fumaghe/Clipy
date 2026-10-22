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
