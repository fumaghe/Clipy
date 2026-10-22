# STEP 2.1 — Correzioni dai log della demo su Windows

Origine: demo reale su Windows (Python 3.13, FFmpeg 9.0.2): 6 clip importate, video generati, Review Gate, 3 pubblicazioni **simulate** (DRY RUN sempre attivo, nessuna pubblicazione reale, nessun dato eliminato, DB non toccato).
Ogni punto ha una diagnosi, la correzione e i test di regressione (`server/app/tests/test_windows_demo_fixes.py`, `frontend/src/__tests__/media.test.tsx`).

## 1. `ConnectionResetError: [WinError 10054]` durante la riproduzione
**Diagnosi.** L'endpoint `GET /api/v1/clips/{id}/video` usa `FileResponse` di Starlette: gestisce correttamente `Range` (200 / **206** con `Content-Range` / **416**), `Accept-Ranges`, `ETag`. Non c'era un difetto nei byte serviti. Il messaggio nasce quando il **browser chiude il socket** a metà stream (seek, `preload="metadata"` annullato, modale chiusa): sul `ProactorEventLoop` di Windows asyncio registra a livello ERROR la `ConnectionResetError` sollevata da `_ProactorBasePipeTransport._call_connection_lost`. È quindi un annullamento normale lato client **amplificato** da due cause nostre:
- ogni card della Review Inbox / Library montava subito 1–2 `<video preload="metadata">`: decine di connessioni Range avviate e annullate subito;
- l'URL del video non cambiava dopo un nuovo render → cache/Range incoerenti.

**Correzioni.**
- Player a richiesta (`ClipMedia.tsx`): il `<video>` e le richieste Range nascono solo al click; allo smontaggio lo stream viene rilasciato (`pause` + `removeAttribute('src')` + `load()`); gli URL portano `?v=<content_version>`; un errore reale di riproduzione mostra un messaggio con «riprova» (l'abort è ignorato).
- `GET` **e `HEAD`** su `/video` e `/preview` (prima `HEAD` dava 405: alcuni player lo usano per sondare dimensione e Range).
- `ClientDisconnectFilter` (`main.py`): sul solo logger `asyncio`, un'eccezione di tipo `ConnectionResetError/ConnectionAbortedError/BrokenPipeError` **proveniente dai callback del transport** viene riportata a DEBUG. **Qualunque altra eccezione o messaggio resta ERROR** (test dedicato): non si nasconde nulla di realmente importante.
- Windows, file aperti: `retention.cleanup` non si interrompe più se un file è ancora in streaming (WinError 32/5) ma rinvia con un evento `file_in_use`; il download atomico (`part.replace`) segnala `download_file_busy` (ritentabile) invece di un errore inatteso. I render hanno sempre un percorso nuovo per versione, quindi non vengono mai sovrascritti mentre sono letti.

**Verifica.** Range 206/416/suffisso/aperto/HEAD con confronto byte-per-byte; test con un vero server uvicorn che chiude 5 connessioni con RST a metà stream (`SO_LINGER 0`): il server resta sano, la richiesta successiva risponde 206 e nessun record ERROR viene emesso. *Non eseguibile qui:* il `ProactorEventLoop` Windows — il messaggio originale va riverificato sul tuo PC (vedi sotto).

## 2. Preview 404 su file non ancora generati
**Diagnosi.** La Library chiedeva `preview?kind=original` anche per clip `DISCOVERED`/`WAITING_FOR_MEDIA`; il 404 faceva scattare `onError` e `Thumb` restava in errore **per sempre** anche quando il file compariva.
**Correzioni.** `Thumb` non richiede nulla finché non esiste un file (`has_render`/`has_original`) e mostra un placeholder per stato («In attesa di download», «Twitch sta preparando il file», «Render in corso…»); usa l'originale finché manca il render; l'errore non è più «incollato» (cambia l'URL → riprova). Il backend risponde `404 media_not_ready` con `Cache-Control: no-store` (non è un errore applicativo, non genera eventi/log di errore). Il `GET /clips` espone `busy`, `busy_reason`, `pending`.

## 3. `AUTO_READY → DOWNLOADED non previsto`
**Diagnosi.** `POST /clips/{id}/analyze` portava la clip a `DOWNLOADED` da qualsiasi stato; la tabella non lo prevedeva (evento `illegal_transition`). Inoltre da `REJECTED/SKIPPED/FAILED` la richiesta era un **no-op silenzioso** e il controllo di stato non era atomico rispetto al worker.
**Correzioni** (`pipeline.reprocess`, `pipeline.exclusive`):
- Transizioni di reprocessing **dichiarate** nella tabella: `AUTO_READY/NEEDS_REVIEW/APPROVED/REJECTED/SKIPPED → DOWNLOADED` (rianalisi) e `→ ANALYZED` (nuovo render), annotate `reprocess:analyze|render`.
- **Atomico**: lock di riga (lo stesso del worker) + una sola transazione; se il worker sta lavorando → 409 `busy`; il worker rilegge lo stato dopo il lock e ignora la selezione obsoleta.
- **Idempotente**: una richiesta ripetuta con la clip già in coda non fa nulla.
- **Senza duplicati**: ogni reprocessing incrementa `content_version` (nuovo file di render, nuovo job per la nuova versione), annulla i soli job locali/simulati (`QUEUED_LOCAL/RETRY_PENDING/DRY_RUN_OK`); se esiste un job inviato o possibilmente inviato (`UPLOADING_ASSET/SCHEDULING/SCHEDULED_REMOTE/PUBLISHED/NEEDS_REVIEW` o con `remote_post_id`) o in lavorazione → 409 `publication_locked`. Lo stesso controllo protegge **modifica testi e rigenerazione** (prima un caption edit su una clip già pianificata creava una nuova versione ⇒ secondo post).
- **Senza perdite**: analisi, review, transizioni, file e job annullati restano; titolo/caption/hashtag precedenti (anche modificati a mano) sono salvati in una `Review` (`policy_version="reprocess"`, `changes.previous`). Il cambio template e il re-render sono nella stessa transazione.

## 4. `POST /clips/{id}/render → 409 Conflict`
**Diagnosi.** Il 409 `busy` è **previsto**: la clip è in `ANALYZING/RENDERING` (o il worker ne detiene il lock). Mancavano però la segnalazione e il blocco dei comandi nell'interfaccia, e il controllo era basato solo sullo stato (finestra di race tra lock del worker e cambio di stato; `skip`/modifica testi/rigenera erano senza protezione).
**Correzioni.** Un'unica fonte (`pipeline.busy_reason` / `exclusive`) usata da render, analyze, skip, cambio template, modifica testi e rigenerazione; messaggi chiari («Render in corso: attendi che finisca e riprova»). UI: Analyze/Render/Titolo/Coda/Skip (Library) e Approva/Salva/Rigenera/Riprova render (Review Inbox) sono disattivati con tooltip e riga «⏳ …»; un 409 appare come avviso ambra (non errore rosso) e ricarica lo stato. Due click ravvicinati su Render non producono due render.

## Cosa NON è stato verificato (onestà)
- Il messaggio WinError 10054 e il filtro su `ProactorEventLoop` non sono riproducibili in sandbox Linux: il filtro è testato con record sintetici identici a quelli di asyncio e il comportamento del server con RST reali; **riprova la demo su Windows** e controlla `data/logs/clipy.log` (non devono più comparire ERROR di reset; a livello DEBUG sì).
- Nessuna chiamata a Twitch/Buffer reale: DRY RUN invariato.

## Prima di collegare Twitch reale
Nessun problema noto nei flussi di elaborazione e riproduzione. Segui `docs/LIVE_TEST_CHECKLIST.md`; il file originale è scaricato in modo atomico (`.part` → rename) e validato con ffprobe prima di essere reso disponibile alla UI.
