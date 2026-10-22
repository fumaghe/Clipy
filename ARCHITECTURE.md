# Architettura di Clipy

Monorepo single-user, local-first, trasferibile su server.

```
frontend/ (React+TS+Vite+Tailwind+TanStack Query)  ──HTTP /api/v1──►  server/app (FastAPI)
                                                                       ├─ api/          router REST + DTO
                                                                       ├─ services/     collector, pipeline, reviews, queue, publishing, retention, settings, factory (DI)
                                                                       ├─ domain/       states, review_gate (puro), schedule (puro), captions
                                                                       ├─ adapters/     twitch | ai | video | storage | publisher  (+ Mock*)
                                                                       ├─ workers/      runner (APScheduler, leader lease)
                                                                       └─ db/           SQLAlchemy 2 + Alembic (SQLite WAL)
data/ originals/ rendered/ previews/ temp/ public/ fixtures/ clipy.db
```

## Contratti (Protocol) e implementazioni
| Contratto | Reale | Demo/test |
|---|---|---|
| `ClipSource` | `HelixClient` (Helix, OAuth utente + app token) | `MockTwitch` (fixture generate con FFmpeg) |
| `Downloader` | `HttpDownloader` (stream, limite, rename atomico) | `FixtureDownloader` |
| `Transcriber` | `FasterWhisperTranscriber` (locale, CPU/GPU) | `MockTranscriber` (SIMULATO) |
| `ContentAnalyzer` | `LLMAnalyzer` (Anthropic / OpenAI-compatibile, vision opzionale) | `MockAnalyzer` (SIMULATO) |
| `VideoRenderer` | `FFmpegRenderer` (filtergraph unico per render e preview) | — |
| `MediaStorage` | `S3Storage` (R2/S3, URL pubblico stabile, no presigned) | `MockStorage` (locale, non pubblico) |
| `Publisher` | `BufferPublisher` (GraphQL) | `MockPublisher` (ciclo di vita simulato) |
| `JobScheduler` | `workers/runner.py` (APScheduler + lease) | tick richiamabili nei test |

`services/factory.py` costruisce gli adapter da impostazioni+segreti; `OVERRIDES` permette l'iniezione nei test. Nessuna dipendenza circolare: `domain` non importa nulla di infrastrutturale.

## Pipeline di una clip
`DISCOVERED → WAITING_FOR_MEDIA → DOWNLOADED → ANALYZING → ANALYZED → RENDERING → READY → (AUTO_READY | NEEDS_REVIEW) → APPROVED | REJECTED`
(+ `SKIPPED`, `FAILED`). Ogni step è idempotente, protetto da lease per riga (`locked_until`) e ripartibile dopo un crash. Il render scrive su `.part` e rinomina atomicamente.
Ogni transizione è registrata in `state_transitions`; ogni evento in `events` (messaggi sanificati).

## Review gate (`domain/review_gate.py`)
Funzione pura e deterministica, versione `gate-1.0`. Segnali (ognuno con motivazione esplicita in `reasons[]`): analisi AI fallita (`analysis_failed`), trascrizione assente/inutilizzabile (`asr_unavailable`, `transcript_unusable`), termini sensibili, caption malformata/URL, ffprobe/safe-area/sincronia A-V del render, audio, evidenze (trascrizione/frame/metadata), confidenza (solo uno dei segnali), flag di analisi,
PII (email/telefono/IBAN), parole vietate, numeri non supportati dalle evidenze, grounding del titolo, titoli duplicati, crop landscape, analisi simulata su clip reale. Output: `AUTO_READY` o
`NEEDS_REVIEW` con `reasons[]` leggibili. Le clip in review non consumano slot.

## Coda e pubblicazione
`queue.rebuild` (idempotente) crea al massimo un job per `(clip, canale, versione contenuto)` e assegna gli slot FIFO con `calculate_next_slots` (pura, DST-safe, tetto giornaliero; numero e orari configurabili, default 4 post/giorno 10:00·13:30·17:30·21:00 `Europe/Rome`).
`publishing.tick`: recupero upload interrotti → rebuild → dispatch entro `lookahead_hours` → riconciliazione. Stati locali: `QUEUED_LOCAL → UPLOADING_ASSET → SCHEDULING → SCHEDULED_REMOTE → PUBLISHED` (+ `RETRY_PENDING`, `NEEDS_REVIEW`, `FAILED`, `CANCELLED`, `DRY_RUN_OK` = simulato).

**Stato remoto granulare** (`remote_state` + `verification`, indipendenti dallo stato locale):
`LOCAL` · `SENDING` (richiesta in corso o esito da riconciliare, oppure Buffer "sending") · `SCHEDULED_ON_BUFFER` · `BUFFER_REPORTS_SENT` (conferma generica del provider) · `BUFFER_REPORTS_ERROR` · `EXTERNALLY_VERIFIED` (solo con conferma manuale dell'utente) · `UNCERTAIN`.
`PUBLISHED` significa **"Buffer segnala inviato"**; la visibilità su TikTok non è mai dichiarata verificata senza conferma esplicita.

**Idempotenza e concorrenza**
- `process_job` acquisisce un lock di riga e **rilegge lo stato sotto lock**: una lista di job obsoleta (altra istanza, tick sovrapposti) non può rifare `createPost`.
- L'intenzione di invio (`SCHEDULING`, `send_started_at`) è persistita **prima** di `createPost`.
- Esito incerto (timeout, 5xx, risposta illeggibile, successo senza ID, crash): **nessuna seconda create automatica**. Si cerca il post su Buffer (paginazione completa): 1 riscontro con testo identico → adottato; più riscontri, o post allo stesso orario con testo diverso, o ricerca incompleta → `NEEDS_REVIEW`; nessun riscontro → serve un periodo di grazia (5 min) e ≥ 2 ricerche complete e vuote prima del retry sicuro.
- Rifiuti certi prima dell'esecuzione (429, DNS/connessione, 4xx, `MutationError`) non sono ambigui: backoff (`Retry-After` rispettato) o revisione.
- Slot scaduto prima dell'invio → il job torna in coda locale e prende il prossimo slot (mai `dueAt` nel passato).
- Lease: leader unico del worker (lease nel DB, TTL 60 s). I lock orfani si liberano solo da chi è l'unico leader (all'acquisizione della leadership e all'avvio se nessun altro leader è vivo): una seconda istanza non azzera i lock di quella attiva.

## Sicurezza
Bind `127.0.0.1`, CORS limitato, segreti solo nel DB locale (mai restituiti dalle API né loggati), `live_enabled`/`is_connected` modificabili solo da endpoint dedicati con frase di conferma,
accesso ai file solo sotto `data/`, import clip da ID (nessuna richiesta verso URL arbitrari), trascrizione/frame trattati come dati non fidati nel prompt.
Non esporre la dashboard in rete senza autenticazione e HTTPS (vedi `docs/OPERATIONS.md`).

## Verso il cloud
Nessun path assoluto in DB (solo sotto `CLIPY_DATA_DIR`), `CLIPY_DATABASE_URL` consente un altro DB, `MediaStorage`/`Publisher` sono intercambiabili. Autenticazione utente da aggiungere prima dell'esposizione.
