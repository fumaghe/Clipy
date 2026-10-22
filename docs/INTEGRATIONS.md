# Integrazioni (setup reale)

> Tutti i passaggi sottostanti sono **NEEDS_LIVE_VERIFICATION**: la documentazione ufficiale non era raggiungibile in fase di sviluppo (vedi `DECISIONS.md`). Verifica i nomi aggiornati su ciascuna console.

## 1. Twitch
1. https://dev.twitch.tv/console → *Register Your Application*. Redirect URI: `http://localhost:8000/api/v1/integrations/twitch/callback` (identico a `TWITCH_REDIRECT_URI`). Categoria: Application Integration.
2. Settings → Twitch: inserisci **Client ID** e **Client Secret** → «Collega account Twitch» e accedi come `iDensTv` (scope `channel:manage:clips`).
3. «Testa connessione»: risolve il login in `broadcaster_id` via API (non viene mai indovinato).
4. «Importa ora» (primo avvio: ultime 24 h), poi Overview → *Start collector* (polling ogni 5 min con overlap 24 h e upsert su `source_clip_id`).
5. Verifica una clip creata da uno spettatore: deve comparire (nessun filtro su `creator_id`).

## 2. LLM
AI Studio → provider (Anthropic o OpenAI-compatibile), modello, API key. Vision opzionale (aumenta i costi; budget/clip configurabile, stima mostrata). Senza chiave l'analisi è simulata e le clip reali vanno in revisione.

## 3. Trascrizione locale
`.venv/bin/pip install faster-whisper` (primo avvio scarica il modello in `data/models`; serve rete una volta). GPU: installare CUDA/cuDNN compatibili con CTranslate2, altrimenti CPU (`int8`).

## 4. Buffer + TikTok
1. Account Buffer → collega TikTok (Clipy non chiede mai la password TikTok).
2. Genera una API key personale (impostazioni sviluppatore Buffer) → Settings → Buffer → incolla.
3. «Testa e elenca canali» (nessun post creato) → «Usa questo» sul canale TikTok: viene verificato servizio e stato connessione.
4. Buffer Free: max ~10 post pianificati per canale → `remote_max_scheduled=10`; il resto resta in coda locale e viene inviato progressivamente.
5. Se il canale è notification-only o un contenuto richiede passaggio manuale, il job va in `NEEDS_REVIEW` e **non** è considerato pubblicato.

## 5. Storage pubblico (Cloudflare R2 / S3)
Buffer non accetta upload: serve un URL HTTPS pubblico, diretto e stabile fino allo slot.
1. R2 → crea bucket, abilita accesso pubblico (dominio custom o `r2.dev`), crea token API S3.
2. Settings → Storage: provider S3, bucket, endpoint `https://<account>.r2.cloudflarestorage.com`, URL pubblico, access key/secret.
3. «Testa upload + accesso pubblico»: carica un file di prova, lo scarica **senza credenziali** (HEAD/GET) e lo cancella.
4. Privacy: i video sono accessibili a chiunque abbia l'URL (nome casuale a 128 bit) fino al cleanup (`media_cleanup_hours` dopo la conferma di invio). Non usare presigned URL che scadono prima dello slot.

## 6. Attivazione live (una sola volta)
Settings → Pubblicazione reale: spunta di consenso + frase `ABILITA PUBBLICAZIONE REALE`. Prima fai un test supervisionato con un video di prova e controlla su Buffer. Disattivabile in qualsiasi momento (torna in DRY RUN).
