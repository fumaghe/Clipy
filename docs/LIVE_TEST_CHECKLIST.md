# Checklist di collaudo live (Windows)

Guida passo-passo per collegare i tuoi account reali a Clipy **in sicurezza**. Segui i passaggi **nell'ordine**: ognuno indica il risultato atteso e cosa fare se qualcosa va storto.

> **Regola d'oro:** finché non arrivi al passo 9 Clipy resta in **DRY RUN** (nessun post viene creato, nessun file viene caricato per la pubblicazione). La pubblicazione reale si abilita solo da *Settings → Pubblicazione reale* con la frase di conferma. Se in qualsiasi momento hai dubbi: *Settings → Pubblicazione reale → Disattiva*.
>
> **Stato di validazione:** tutte le integrazioni reali (Twitch, Buffer, R2, LLM, Whisper) sono state sviluppate **senza accesso ai servizi** e verificate solo con simulazioni. Questa checklist è la loro prima verifica reale. Gli script Windows non sono mai stati eseguiti su Windows prima di questo collaudo.

Cosa ti serve: un PC Windows 10/11, connessione internet, ~5 GB liberi, i tuoi account Twitch (`iDensTv`), Buffer (con TikTok collegato), un provider LLM (Anthropic o compatibile OpenAI) e Cloudflare.

Se qualcosa non funziona, per chiedere aiuto condividi **solo**: il testo dell'errore, `data\logs\clipy.log` (gli errori sono già mascherati, ma controlla prima di inviarlo) e l'output di *Settings → Diagnostica integrazioni*. **Mai** chiavi API, Client Secret, token o il file `data\clipy.db`.

---

## 1. Avviare Clipy

**Azioni**
1. Scarica/clona il repository in una cartella utente, es. `C:\Users\<tu>\Clipy` (evita *Program Files*; gli spazi nel percorso sono gestiti).
2. Installa i prerequisiti (PowerShell, una volta sola; poi **chiudi e riapri** il terminale):
   ```powershell
   winget install Python.Python.3.12 OpenJS.NodeJS.LTS Gyan.FFmpeg
   ```
3. Facoltativo ma consigliato, controllo preliminare (non modifica nulla):
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\scripts\check-windows.ps1
   ```
4. Doppio clic su **`start-clipy.bat`**.

**Risultato atteso**
- `check-windows` mostra righe `[OK]` per Python 3.11+, Node ≥ 20, FFmpeg (con `drawtext`), porta 8000 libera, cartella dati scrivibile.
- Al primo avvio installa dipendenze Python/npm e compila il frontend (alcuni minuti), poi si apre il browser su `http://127.0.0.1:8000`.
- La finestra nera resta aperta (è il server: **non chiuderla**; `CTRL+C` per fermarlo in modo pulito).
- Overview mostra **modalità demo** e **DRY RUN**.

**Se qualcosa va storto**

| Sintomo | Causa probabile | Cosa fare |
|---|---|---|
| `[FAIL] Python 3.11+ non trovato` | Python assente, oppure solo lo "stub" del Microsoft Store | `winget install Python.Python.3.12`, riapri il terminale. Disattiva gli alias *App execution aliases* di python.exe in Impostazioni di Windows se persiste |
| `[FAIL] ffmpeg/ffprobe non trovati` | PATH non aggiornato dopo l'installazione | Riapri il terminale. Se è in una cartella non standard: `setx CLIPY_FFMPEG_DIR "C:\percorso\ffmpeg\bin"` e riapri |
| `[FAIL] ... drawtext` | Build FFmpeg senza `drawtext` | Installa la build *full* (`Gyan.FFmpeg`) |
| `[FAIL] Porta 8000 già in uso` | Altra istanza di Clipy o altro programma | Chiudi l'altra finestra; oppure `.\scripts\start-dev.ps1 -Prod -Port 8001` |
| La finestra si chiude subito | Non dovrebbe più accadere (il `.bat` fa `pause`) | Se succede, avvia da un terminale PowerShell: `.\scripts\start-dev.ps1 -Prod` e copia l'errore |
| `pip install fallito` / `npm install fallito` | Rete o proxy | Riprova; verifica la connessione; controlla antivirus/proxy aziendale |
| Il browser non si apre | Il backend non risponde a `/health` | Apri manualmente `http://127.0.0.1:8000`; leggi `data\logs\clipy.log` |

---

## 2. Collegare Twitch

**Azioni**
1. https://dev.twitch.tv/console → *Register Your Application*: nome libero, categoria *Application Integration*, tipo client *Confidential*.
2. **Redirect URL (identico, carattere per carattere):** `http://localhost:8000/api/v1/integrations/twitch/callback` (è mostrato anche in *Settings → Twitch*).
3. Copia **Client ID** e genera il **Client Secret**.
4. *Settings → Twitch*: incolla Client ID e Client Secret → **Salva**.
5. Premi **Collega account Twitch** e accedi **con l'account `iDensTv`** (o, se usi un altro account, che sia *editor* del canale). Accetta lo scope `channel:manage:clips`.
6. Torna in *Settings*: dovresti vedere "Account Twitch collegato". In *Diagnostica integrazioni* premi **Testa** su Twitch.

**Risultato atteso**
- Banner verde "Account Twitch collegato"; badge *Autorizzato*.
- Diagnostica Twitch → **Connesso**: "Token valido per idenstv (scope OK)".
- Nessun banner arancione "account diverso".

**Se qualcosa va storto**
- *Errore sul sito Twitch "redirect_mismatch"*: il Redirect URL registrato non è identico → correggilo nella console Twitch.
- Banner rosso "scope": hai tolto il permesso → ripeti **Collega** accettando tutto. Il token **non** viene salvato senza lo scope giusto.
- Banner arancione "account diverso": hai autorizzato un account diverso dal canale. Funziona solo se quell'account è editor di `iDensTv` (verificabile al passo 3 scaricando una clip).
- Diagnostica **Test fallito** "riconnetti l'account": token revocato/scaduto e non rinnovabile → **Collega** di nuovo.
- Diagnostica "Da verificare live": identità diversa dal canale (vedi sopra).
- Poi: *Settings → Twitch → Esci dalla modalità demo* (se non già uscito: il salvataggio del Client ID lo fa da solo).

---

## 3. Importare clip reali

**Azioni**
1. *Settings → Twitch → Importa ora* (al primo avvio importa le clip delle ultime 24 h, configurabile).
2. Controlla *Clip Library*: devono comparire le clip di `iDensTv`, **comprese quelle create dagli spettatori**.
3. Lascia lavorare la pipeline (Overview → *Run once* oppure attendi): le clip passano da *Scoperta* a *Scaricata*.
4. Quando sei pronto per il monitoraggio continuo: Overview → **Start collector** (polling ogni 5 min, con overlap di 24 h e deduplica per ID).

**Risultato atteso**
- Messaggio "Import: N clip, M nuove" (nessun simulato).
- Colonna creator con nomi di spettatori reali.
- Le clip scaricate hanno un file in `data\originals` ; stato *Scaricata*. Se Twitch non ha ancora il formato verticale, la clip resta *In attesa media* fino a 10 minuti (`portrait_wait_minutes`), poi viene scaricata in landscape e convertita in 9:16.
- Rilanciare "Importa ora" non crea doppioni.

**Se qualcosa va storto**

| Sintomo | Cosa fare |
|---|---|
| `Twitch 403` / `twitch_forbidden` sul download | L'account autorizzato non è broadcaster/editor, o manca lo scope → ricollega con l'account giusto |
| Nessuna clip trovata | Nessuna clip nelle ultime 24 h: aumenta *Backfill (ore)* e ripeti, oppure importa una clip specifica con *Importa clip* (ID o URL) |
| Clip bloccate in *In attesa media* | Normale per i primi minuti. Oltre 6 tentativi diventa *Errore*: Library → **Retry** |
| "File scaricato incompleto o corrotto" | Viene riscaricato da solo con un link nuovo; se ripetuto, Library → Retry |
| "Rate limit Twitch" | Attendi: il retry è automatico con backoff |
| Errore `twitch_not_authorized` in Overview | Ricollega l'account (passo 2) |

Se l'endpoint di download clip di Twitch rifiuta le richieste o ha un formato diverso da quello implementato, manda l'errore esatto dal log: il codice è in `server/app/adapters/twitch/helix.py`.

---

## 4. Configurare Whisper (trascrizione locale)

**Azioni**
1. Installa: `.\.venv\Scripts\pip install faster-whisper` (se fallisce su Python 3.13 usa Python 3.12 e ricrea `.venv`).
2. *Settings* → Diagnostica → **Whisper** → **Testa** → conferma. La prima volta scarica il modello (`small` ≈ 500 MB) in `data\models`: serve rete.
3. Per un PC con poca RAM usa il modello `base` (Overview/AI Studio → impostazioni AI → modello Whisper).

**Risultato atteso**
- Diagnostica Whisper → **Connesso**: "Modello caricato e trascrizione di prova eseguita".
- Nelle clip analizzate compare la trascrizione (AI Studio / Review Inbox).
- Una clip senza parlato è gestita (nessun testo inventato).

**Se qualcosa va storto**
- Non installato: le clip reali vanno in **Review Inbox** con il motivo "Trascrizione non disponibile". È voluto: Clipy non inventa mai una trascrizione.
- *Test fallito: modello non caricabile*: rete assente per il download, disco pieno, o cache corrotta → cancella `data\models` e ripeti.
- Avviso RAM: usa un modello più piccolo (`base`/`small`).
- Molto lento: è CPU (`int8`). Una GPU NVIDIA con CUDA/cuDNN compatibili con CTranslate2 è facoltativa; se la GPU dà errore, Clipy ricade su CPU da solo.
- Se non vuoi usare Whisper: imposta la trascrizione su *off* (le clip si baseranno su titolo/gioco/frame e andranno in revisione se mancano evidenze).

---

## 5. Collegare il provider LLM

**Azioni**
1. Crea una API key presso il provider (Anthropic o compatibile OpenAI).
2. *Settings/AI Studio*: provider, **modello** (obbligatorio), eventuale *base URL* (per provider compatibili), API key → salva.
3. Diagnostica → **Provider LLM** → **Testa** → conferma (una richiesta minima, costo trascurabile).
4. Imposta il budget per clip (`budget_usd_per_clip`, default 0,05 $; `0` = nessun limite) e, se vuoi, abilita *vision* (più costoso).
5. Analizza una clip reale (Library → clip → Rigenera / AI Studio) e **leggi titolo e caption**.

**Risultato atteso**
- Diagnostica LLM → **Connesso** con latenza e token.
- Titolo/caption coerenti con quello che accade davvero nella clip, lunghezza entro i limiti, ≤ 5 hashtag, emoji limitate.
- Clip con titolo non supportato dalla scena finiscono in **Review Inbox** con il motivo.

**Se qualcosa va storto**
- `Chiave LLM rifiutata` → controlla chiave e che appartenga al provider scelto.
- `Costo stimato oltre il budget` → alza il budget o disattiva vision.
- Output non valido ripetuto → la clip va in **Review Inbox** ("Analisi AI non riuscita") con un titolo basato solo sul titolo Twitch: usa **Rigenera** o modifica a mano.
- Titoli inventati/poco fedeli → alza il preset della review gate a *Prudente*, aggiungi istruzioni in *prompt aggiuntivo* e segnalamelo con 2-3 esempi.
- Rate limit → retry automatico; dopo 4 tentativi la clip va in revisione (non si perde).

---

## 6. Generare il primo video reale

**Azioni**
1. Scegli una clip con parlato chiaro in Library. Attendi *Pronta (auto)* o *Da rivedere*.
2. Apri il file in `data\rendered\<id>-v1.mp4` e guardalo **per intero con l'audio**.
3. *Template Studio*: controlla l'anteprima (usa lo stesso motore del render finale). Il template è un punto di partenza **non verificato** rispetto al tuo TikTok di riferimento: ritoccalo se serve.
4. In *Review Inbox* approva, modifica o scarta.

**Risultato atteso** (lista di controllo)
- [ ] Risoluzione 1080×1920, durata uguale alla clip, audio presente e sincronizzato.
- [ ] Titolo leggibile, centrato, **non coperto dall'interfaccia TikTok** (fascia alta ~8% e bassa ~20%), accenti corretti.
- [ ] Nessun bordo nero indesiderato; se la clip era landscape, il crop 9:16 mantiene il soggetto (altrimenti scegli *letterbox* nel template).
- [ ] Il video si apre su telefono/lettore standard.

**Se qualcosa va storto**
- Testo tagliato/fuori safe area → la clip va in revisione (`overlay_outside_safe_area`): accorcia il titolo o sposta `y_percent`.
- Crop sbagliato → Template Studio: offset del crop o modalità *letterbox*.
- Font mancante → compare l'avviso `font_missing`: copia un `.ttf` in `data\fonts` e indicane il nome nel template.
- Render fallito → Library → clip → *Retry render*; se ripetuto, copia l'errore dall'evento.

---

## 7. Collegare Buffer / TikTok

**Azioni**
1. Su buffer.com collega il tuo TikTok (Clipy non chiede mai la password TikTok).
2. Genera una **API key** personale nelle impostazioni sviluppatore Buffer.
3. *Settings → Buffer*: incolla la key → **Testa e elenca canali** (sola lettura, nessun post).
4. Diagnostica → **Buffer / TikTok** → **Testa**: esegue anche la *verifica dello schema* GraphQL (introspezione, sola lettura).
5. Seleziona il canale TikTok con **Usa questo** (vengono salvati canale **e** organizzazione).

**Risultato atteso**
- Elenco di organizzazioni e canali; i canali non TikTok o disconnessi sono marcati e non selezionabili.
- Diagnostica → **Connesso** ("Lettura riuscita e schema compatibile") **oppure** *Da verificare live* se Buffer non permette l'introspezione.
- Badge Buffer "Collegato" e canale selezionato.

**Se qualcosa va storto**
- `API key non valida` → rigenera la key. Cambiare la key scollega il canale e riporta in DRY RUN (voluto).
- *Test fallito: Schema Buffer diverso dall'atteso* → **è la differenza che temevamo**: copia il messaggio completo; l'adapter è `server/app/adapters/publisher/buffer.py` (query in un punto solo). **Non abilitare il live.**
- Nessun canale TikTok → il canale non è collegato a Buffer o è in un'altra organizzazione.
- Canale "disconnesso" → ricollegalo da Buffer.
- *Note importanti non validate:* i metadati specifici di TikTok (privacy, interazioni) e l'idoneità del canale alla pubblicazione automatica (alcuni canali possono richiedere un passaggio manuale) **non sono verificati**: li scoprirai al passo 9. Se Buffer segnala `draft`/`needs_approval`, il job va in revisione e non viene considerato pubblicato.

---

## 8. Configurare R2 (storage pubblico)

**Azioni**
1. Cloudflare → R2 → crea un bucket (es. `clipy-media`).
2. Abilita l'accesso pubblico: dominio personalizzato (consigliato) oppure `r2.dev`. Annota l'**URL pubblico HTTPS**.
3. Crea un **API token S3** con permessi *Object Read & Write* limitato a quel bucket; annota Access Key ID, Secret e l'endpoint `https://<account>.r2.cloudflarestorage.com`.
4. *Settings → Storage*: provider *S3 / Cloudflare R2*, bucket, endpoint, URL pubblico, chiavi → **Salva**.
5. Diagnostica → **Storage R2/S3** → **Testa** → leggi e conferma l'avviso (carica un file di prova, lo scarica **senza credenziali**, lo cancella).

**Risultato atteso**
- Diagnostica **Connesso** con i quattro riscontri: upload ✔, accesso anonimo HTTPS ✔, cancellazione ✔, file non più raggiungibile ✔.

**Se qualcosa va storto**
- `HTTP 403` sull'accesso anonimo → l'accesso pubblico del bucket non è attivo (o il dominio non punta al bucket).
- `Upload fallito` → endpoint/chiavi/permessi errati.
- "Cancellazione non andata a buon fine" → il token non ha il permesso di delete: aggiungilo e ripulisci a mano il file `clipy-test-*.mp4` dal bucket.
- URL non HTTPS → Buffer lo rifiuta: usa HTTPS.
- Privacy: i video caricati sono pubblici (nome casuale a 128 bit) fino alla pulizia successiva alla pubblicazione (`media_cleanup_hours`).

---

## 9. Pubblicazione supervisionata (UN solo post)

Prerequisiti: passi 1-8 completati, diagnostica Buffer e Storage **Connesso** (o Buffer *Da verificare live*, consapevole che questo è proprio il test).

**Azioni**
1. Imposta *Settings → Pianificazione*: **Post al giorno = 1** e **Max post remoti pianificati = 1**.
2. Scegli **una** clip che ti piace, approvala in Review Inbox. Controlla in *Coda* che ci sia **un solo** job locale; rimetti in pausa/annulla gli altri.
3. *Settings → Pubblicazione reale*: spunta il consenso, digita `ABILITA PUBBLICAZIONE REALE` → **Abilita**.
4. Premi **Invia ora** sul job (oppure attendi lo slot). Clipy: carica il video su R2, verifica l'URL, crea il post su Buffer.
5. Apri **Buffer** e controlla: il post è presente, con la tua caption, data/ora corrette (in UTC convertita) e il video giusto.
6. Dopo l'orario, verifica **su TikTok** che il video sia visibile sul profilo. Solo allora, in *Storico*, premi **Ho verificato su TikTok**.
7. **Disattiva** subito il live (*Settings → Pubblicazione reale → Disattiva*) finché non sei soddisfatto.

**Risultato atteso**
- Job: *Coda locale → Upload media → Pianificazione… → Pianificato (remoto)* con badge **Programmato su Buffer**.
- Dopo la pubblicazione Buffer: **Inviato (Buffer)** + badge *Buffer segnala inviato* — questa è una conferma del provider, **non** una verifica di TikTok. Diventa *Verificato su TikTok* solo con la tua conferma.
- Nessun secondo post, anche riavviando Clipy durante il test.

**Se qualcosa va storto**
- *Rifiutato da Buffer* (`buffer_mutation_error`) → il job va in **Revisione** con il messaggio di Buffer; non viene ritentato alla cieca. Probabili cause: forma dell'input (`assets`), canale non idoneo, limiti. Copia il messaggio.
- *Timeout / "Esito della create sconosciuto"* → **non fare nulla subito**: Clipy riconcilia da solo (cerca il post su Buffer) e ritenta soltanto dopo ricerche complete e vuote. **Controlla tu su Buffer** se il post esiste prima di premere *Riprova*. Se il job finisce in *Da rivedere* per "riscontri ambigui" o "ricerca incompleta", decidi tu guardando Buffer.
- Il post esiste su Buffer ma non su TikTok → apri il post in Buffer e leggi l'errore di pubblicazione (lo stato `error` viene riportato in Clipy come *Buffer segnala errore*).
- Per annullare un post già pianificato: **da Buffer**, poi *Riconcilia* in Clipy.
- Orario sbagliato → controlla fuso (`Europe/Rome`) e che gli slot non siano scaduti.

---

## 10. Attivare la pubblicazione automatica

Fallo solo dopo il passo 9 riuscito **e** verificato. Condizioni consigliate (go/no-go):
- [ ] ≥ 3 pubblicazioni supervisionate consecutive visibili su TikTok, **0 doppioni**.
- [ ] Ho riavviato Clipy almeno una volta con post pianificati e non è cambiato nulla.
- [ ] Review gate su *Prudente*; ho guardato cosa finisce in Review Inbox per qualche giorno.
- [ ] Ho un modo per accorgermi dei problemi: guardo *Overview → Ultime attività* e *Storico* ogni giorno nelle prime settimane.

**Azioni**
1. *Settings → Pianificazione*: ripristina i **4 post/giorno** e gli slot 10:00, 13:30, 17:30, 21:00 (`Europe/Rome`); *Max post remoti* in linea con il tuo piano Buffer (Free: 10).
2. Riabilita il live (*Settings → Pubblicazione reale*) se l'avevi disattivato.
3. Overview → **Start collector**. Lascia il PC acceso: finché Clipy gira in locale, il worker elabora e pianifica; i post già programmati su Buffer partono anche a PC spento.

**Risultato atteso**
- Le clip idonee arrivano in coda da sole; solo le dubbie finiscono in Review Inbox con una motivazione; la coda non si blocca per una clip dubbia.

**Come tornare indietro in ogni momento**
- *Settings → Pubblicazione reale → Disattiva* (nuovi invii bloccati, DRY RUN).
- *Stop collector* in Overview.
- Post già pianificati: cancellali da Buffer e poi *Riconcilia* in Clipy.

---

## Appendice — Cosa NON è ancora stato verificato (sarai tu a farlo)

| Area | Stato |
|---|---|
| Endpoint Twitch reali (clips, download portrait/landscape), OAuth | Solo MockTransport — passi 2-3 |
| Whisper con modello vero | Non eseguito nel sandbox — passo 4 |
| Provider LLM reali | Solo trasporto simulato — passo 5 |
| Buffer: query `account/organizations`, `channels`, `posts`, mutation `createPost` (`assets`) | Allineati alla documentazione fornita, **mai eseguiti** — passi 7 e 9 |
| Metadati TikTok specifici, idoneità canale | Non implementati / non validati — passo 9 |
| R2 reale | Solo client finto — passo 8 |
| Script Windows (`start-clipy.bat`, `check-windows.ps1`, `start-dev.ps1`) | Mai eseguiti su Windows — passo 1 |
| Protezione segreti con DPAPI | Logica testata con backend finto; chiamate Windows reali non eseguite — verifica al passo 1-2 che i segreti restino leggibili dopo un riavvio |
