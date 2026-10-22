# Operazioni

- **Worker**: avviato nel processo FastAPI se `CLIPY_RUN_WORKER=true`. Un solo leader per volta (lease `worker` nel DB, TTL 60 s): due processi o il reload di uvicorn non duplicano i job. Evita `--reload` in produzione locale.
- **Tick**: collector (controlla ogni 30 s, scansiona ogni `polling_minutes`), pipeline (5 s, **un solo render alla volta**), publisher (20 s), retention (6 h).
- **Log**: stdout + file `data/logs/clipy.log` (ruotato, 4×2 MB) + tabella `events` (Overview → Ultime attività, `/api/v1/system/events`). I messaggi sono sanificati (token/chiavi mascherati).
- **Backup**: ferma il processo e copia `data/clipy.db` (+ `-wal`/`-shm`) oppure usa `sqlite3 data/clipy.db ".backup backup.db"` a caldo. I file in `data/originals|rendered` sono ricostruibili solo se la clip è ancora su Twitch.
- **Ripristino**: sostituisci `data/clipy.db`; al riavvio i lock orfani vengono azzerati e i job riprendono dallo stato persistito.
- **Retention**: `retention_days` (default 30) elimina originali/render di clip scartate/saltate/pubblicate vecchie; DB e audit restano; clip in coda non vengono toccate.
- **Token Twitch scaduti**: refresh automatico; se fallisce compare l'errore `twitch_not_authorized` in Overview → ricollega da Settings.
- **Limiti**: Buffer Free ~10 post pianificati/canale (`remote_max_scheduled`); TikTok 25 post/24 h (da ricontrollare); `daily_max` ≤ 25.
- **Sicurezza**: bind predefinito `127.0.0.1`; CORS limitato a `CLIPY_CORS_ORIGINS`. **Non esporre la dashboard in rete** senza reverse proxy con HTTPS + autenticazione (Clipy V1 non ha login utente).
- **Recupero errori**: clip `FAILED` → Library → *Retry*; job `FAILED/NEEDS_REVIEW` → Storico → *Riconcilia*/*Riprova*. Se un job ha già un `remote_post_id`, «Riprova» riconcilia invece di ricreare.
- **Aggiornamento**: `git pull`, rilancia lo script di avvio (installa dipendenze, applica le migrazioni Alembic all'avvio).
- **Fixture demo** in `data/fixtures` (generate con FFmpeg); eliminabili.
- **Esito incerto dopo l'invio** (timeout/5xx/crash durante `createPost`): Clipy **non** ricrea il post. Riconcilia da solo; se il job finisce in *Da rivedere* controlla su Buffer se il post esiste, poi usa *Riconcilia* (se esiste) o *Riprova* (se certamente non esiste).
- **Segreti**: su Windows sono cifrati con DPAPI; `python -m app.tools.secrets_cli status|protect|unprotect` (dalla cartella `server`, con il venv). Un `clipy.db` copiato su un altro PC/utente ha segreti non leggibili: reinseriscili in Settings.
- **File parziali**: `*.part`, `*.part.mp4` e temporanei più vecchi di 1 ora vengono rimossi all'avvio.
- **Accesso**: l'API accetta solo header `Host` locali (`CLIPY_ALLOWED_HOSTS`) e rifiuta richieste che modificano stato con `Origin` estranea.
