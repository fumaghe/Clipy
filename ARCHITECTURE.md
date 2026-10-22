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
Funzione pura e deterministica, versione `gate-1.0`. Segnali: ffprobe/safe-area del render, audio, evidenze (trascrizione/frame/metadata), confidenza (solo uno dei segnali), flag di analisi,
PII (email/telefono/IBAN), parole vietate, numeri non supportati dalle evidenze, grounding del titolo, titoli duplicati, crop landscape, analisi simulata su clip reale. Output: `AUTO_READY` o
`NEEDS_REVIEW` con `reasons[]` leggibili. Le clip in review non consumano slot.

## Coda e pubblicazione
`queue.rebuild` (idempotente) crea al massimo un job per `(clip, canale, versione contenuto)` e assegna gli slot FIFO con `calculate_next_slots` (pura, DST-safe, tetto giornaliero).
`publishing.tick`: rebuild → dispatch entro `lookahead_hours` → riconciliazione. Stati: `QUEUED_LOCAL → UPLOADING_ASSET → SCHEDULING → SCHEDULED_REMOTE → PUBLISHED`
(`PUBLISHED` solo se lo stato remoto lo conferma). `DRY_RUN_OK` = simulato, nessuna richiesta esterna. L'intenzione di `createPost` è persistita *prima* della chiamata; dopo timeout/crash si
cerca il post remoto (adozione se 1, `NEEDS_REVIEW` se ambiguo, retry sicuro se 0).

## Sicurezza
Bind `127.0.0.1`, CORS limitato, segreti solo nel DB locale (mai restituiti dalle API né loggati), `live_enabled`/`is_connected` modificabili solo da endpoint dedicati con frase di conferma,
accesso ai file solo sotto `data/`, import clip da ID (nessuna richiesta verso URL arbitrari), trascrizione/frame trattati come dati non fidati nel prompt.
Non esporre la dashboard in rete senza autenticazione e HTTPS (vedi `docs/OPERATIONS.md`).

## Verso il cloud
Nessun path assoluto in DB (solo sotto `CLIPY_DATA_DIR`), `CLIPY_DATABASE_URL` consente un altro DB, `MediaStorage`/`Publisher` sono intercambiabili. Autenticazione utente da aggiungere prima dell'esposizione.
