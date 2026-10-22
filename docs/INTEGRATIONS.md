# Integrazioni (setup reale)

> **Stato di validazione (STEP 2):** la documentazione ufficiale di Twitch/Buffer non era raggiungibile dal sandbox di sviluppo (DNS bloccato, verificato con curl e WebFetch). Gli adapter sono stati allineati alle strutture **descritte nel brief di STEP 2** (esempi Buffer: get-organizations, get-channels, get-paginated-posts, create-video-post, create-scheduled-post) e testati con trasporti simulati. **Nessuna integrazione è stata validata live.** La procedura passo-passo del collaudo è in [`LIVE_TEST_CHECKLIST.md`](LIVE_TEST_CHECKLIST.md); *Settings → Diagnostica integrazioni* mostra per ogni servizio uno stato distinto (`NOT_CONFIGURED`, `CONFIGURED`, `CONNECTED`, `TEST_FAILED`, `NEEDS_LIVE_VERIFICATION`) e marca come `SIMULATO` ciò che usa mock.

## 1. Twitch
1. https://dev.twitch.tv/console → *Register Your Application*. Redirect URI: `http://localhost:8000/api/v1/integrations/twitch/callback` (identico a `TWITCH_REDIRECT_URI`).
2. Settings → Twitch: **Client ID** e **Client Secret** → «Collega account Twitch» e accedi come `iDensTv` (scope `channel:manage:clips`).
3. Il callback verifica: `state` monouso e a scadenza (10 min, anti-CSRF), scope concesso (senza `channel:manage:clips` il token **non** viene salvato), identità dell'utente (`/oauth2/validate`; se diversa dal canale configurato è segnalato).
4. `editor_id` per i download = **utente proprietario del token** (coincide con il broadcaster se autorizza il proprietario del canale; altrimenti deve esserne editor). `broadcaster_id` è sempre risolto da `login` via API.
5. Token scaduti: su `401` rinnovo con refresh token (rotazione salvata) e un solo nuovo tentativo; refresh revocato → `twitch_not_authorized` (ricollega); errori di rete → retry con backoff.
6. Raccolta clip: finestre ≤ 7 giorni (bisecate se potenzialmente troncate dal tetto di 1000), paginazione completa, filtro su `created_at`, deduplica per `clip_id`, nessun filtro sul creator, clip malformate saltate (contate), cursore avanzato solo a scansione completata (overlap 24 h).
7. Download: preferito `portrait_download_url`; se assente attesa `portrait_wait_minutes`, nuova richiesta di URL ad ogni tentativo, poi landscape convertito 9:16. Scrittura su `.part`, rinomina solo se la dimensione coincide con `Content-Length` **e** `ffprobe` valida il file; un file troncato viene eliminato e riscaricato. Solo URL HTTPS.
8. **Non verificato live:** formato esatto delle risposte di `/helix/clips` e `/helix/clips/downloads`, rate limit reali.

## 2. LLM
AI Studio → provider (Anthropic o OpenAI-compatibile, anche locale via *base URL*), **modello**, API key, timeout (`llm_timeout_seconds`), budget per clip (`budget_usd_per_clip`; stima prudente *prima* della chiamata: se sfora senza frame si rifiuta, con frame si tolgono i frame; `0` = nessun limite). Output JSON validato con Pydantic (titolo e caption obbligatori); un retry con correzione, poi **fallback descrittivo marcato `analysis_failed` → NEEDS_REVIEW** (mai testo inventato). Errori del provider (chiave rifiutata, budget, 5xx persistenti) producono lo stesso esito: la clip prosegue fino alla review con motivo esplicito e **non blocca la coda**. Senza chiave l'analisi è simulata e le clip reali vanno in revisione.

## 3. Trascrizione locale (faster-whisper)
`.\.venv\Scripts\pip install faster-whisper` (Python 3.11–3.13; se l'installazione fallisce su 3.13 usa 3.12). Il modello si scarica in `data/models` al primo uso (serve rete). Fallback CPU (`int8`) automatico se la GPU/cuDNN non funziona. Clip senza traccia audio = muta (nessun testo); audio corrotto, modello non caricabile, errori di decodifica o timeout (`asr_timeout_seconds`, cooperativo tra i segmenti) → `available=False` con la causa: la clip va in revisione (`asr_unavailable`, disattivabile solo spegnendo esplicitamente la trascrizione). Output tipici da allucinazione (sottotitoli/outro, testo ripetuto) → `transcript_unusable`. RAM indicativa: tiny/base ~1 GB, small ~2 GB, medium ~5 GB, large ~10 GB.

## 4. Buffer + TikTok (GraphQL `https://api.buffer.com`)
Schema usato (allineato agli esempi del brief, **NON VALIDATO LIVE**):

| Operazione | Forma |
|---|---|
| Organizzazioni | `query { account { organizations { id name } } }` |
| Canali | `channels(input: { organizationId: "…" }) { id name service isDisconnected }` (per ogni organizzazione; se il campo opzionale è rifiutato si ritenta con `id name service`) |
| Post | `posts(input: { organizationId: "…", filter: { channelIds: ["…"], status: [scheduled, sending] } }, first: 100, after: "…") { edges { node { … } } pageInfo { endCursor hasNextPage } }` — **paginazione completa** (tetto di sicurezza 5000: oltre, la ricerca è dichiarata *incompleta*, mai "vuota") |
| Post singolo | `post(input: { id: "…" })`; se lo schema non combacia, scansione paginata |
| Pubblicazione | `createPost(input: { text, channelId, schedulingType: automatic, mode: customScheduled, dueAt: "<UTC ISO>", assets: { videos: [{ url }] } })` con `… on PostActionSuccess { post { … } }` e `… on MutationError { message }` |

Scelte: i valori dinamici sono **letterali GraphQL escapati** (non variabili): non dipendiamo dai nomi dei tipi di input non verificabili, gli enum sono veri enum e il testo non può uscire dal letterale (testato). `organization_id` e `channel_id` sono salvati insieme nel contesto dell'integrazione; account con più organizzazioni o più canali TikTok sono gestiti (selezione esplicita), canali disconnessi o non TikTok non sono selezionabili. Cambiare la API key scollega il canale e riporta in DRY RUN.

Errori: 400/401/403/429/5xx, timeout, DNS, JSON malformato, `errors` GraphQL (codici `UNAUTHENTICATED`, `RATE_LIMITED`), `data` nullo/incompleto sono classificati; `Retry-After` è rispettato; nessun messaggio contiene la API key. Per la **mutation** ogni esito dopo l'invio della richiesta (timeout, 5xx, risposta illeggibile, successo senza ID) è marcato *ambiguo*: vedi riconciliazione in `ARCHITECTURE.md`.

**Verifica schema senza rischi:** *Diagnostica → Buffer → Testa* esegue introspezione in sola lettura (campi root, `CreatePostInput`) e segnala le differenze. **Non validato:** forma esatta di `assets`, metadati specifici di TikTok (privacy, interazioni), idoneità del canale alla pubblicazione automatica (un canale "notification-only" produrrebbe `draft`/`needs_approval` → job in revisione, non "pubblicato").

Buffer Free: ~10 post pianificati per canale → `remote_max_scheduled=10`; il resto resta nella coda locale.

## 5. Storage pubblico (Cloudflare R2 / S3)
Buffer non accetta upload: serve un URL HTTPS pubblico, diretto e stabile fino allo slot.
1. R2 → bucket, accesso pubblico (dominio custom o `r2.dev`), token API S3 (Object Read & Write sul bucket).
2. Settings → Storage: provider S3, bucket, endpoint `https://<account>.r2.cloudflarestorage.com`, URL pubblico, access key/secret.
3. *Diagnostica → Storage → Testa* (richiede conferma esplicita): carica un file di prova, lo scarica **senza credenziali** via HTTPS, lo cancella e verifica che non sia più raggiungibile.
4. Privacy: i video sono accessibili a chiunque abbia l'URL (nome casuale a 128 bit) fino al cleanup (`media_cleanup_hours` dopo la conferma di invio). Niente presigned URL.

## 6. Attivazione live (una sola volta)
Settings → Pubblicazione reale: consenso + frase `ABILITA PUBBLICAZIONE REALE`. Prima fai il collaudo supervisionato (checklist, passo 9). Disattivabile in qualsiasi momento (torna in DRY RUN).

## 7. Segreti
Salvati nel DB locale (`data/clipy.db`), mai restituiti dalle API né loggati (filtro su tutti i log, anche gli access log di uvicorn). **Windows:** cifrati a riposo con DPAPI (utente corrente) al primo avvio dopo l'aggiornamento — i valori V1 in chiaro vengono migrati senza perdita; copiando `clipy.db` su un altro PC/utente i segreti risultano *non leggibili* e vanno reinseriti (comportamento voluto). Per migrare verso un secret manager/cloud: eseguire `unprotect_all` con lo stesso utente Windows (vedi `docs/DECISIONS.md`) oppure reinserirli. `CLIPY_SECRET_PROTECTION=none` disattiva la cifratura.
