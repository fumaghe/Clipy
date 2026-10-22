const TZ = "Europe/Rome";

export const fmtDateTime = (iso?: string | null) =>
  iso ? new Date(iso).toLocaleString("it-IT", { timeZone: TZ, day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "—";

export const fmtDay = (iso: string) =>
  new Date(iso).toLocaleDateString("it-IT", { timeZone: TZ, weekday: "long", day: "numeric", month: "long" });

export const dayKey = (iso: string) => new Date(iso).toLocaleDateString("sv-SE", { timeZone: TZ });

export const fmtTime = (iso: string) => new Date(iso).toLocaleTimeString("it-IT", { timeZone: TZ, hour: "2-digit", minute: "2-digit" });

export const fmtDur = (s: number) => `${Math.round(s)}s`;

export const STATUS_LABEL: Record<string, string> = {
  DISCOVERED: "Scoperta", WAITING_FOR_MEDIA: "In attesa media", DOWNLOADED: "Scaricata", ANALYZING: "Analisi…", ANALYZED: "Analizzata",
  RENDERING: "Render…", READY: "Pronta", AUTO_READY: "Pronta (auto)", NEEDS_REVIEW: "Da rivedere", APPROVED: "Approvata", REJECTED: "Scartata",
  SKIPPED: "Saltata", FAILED: "Errore", QUEUED_LOCAL: "Coda locale", UPLOADING_ASSET: "Upload media", SCHEDULING: "Pianificazione…",
  SCHEDULED_REMOTE: "Pianificato (remoto)", PUBLISHED: "Inviato (Buffer)", RETRY_PENDING: "Nuovo tentativo", CANCELLED: "Annullato", DRY_RUN_OK: "Simulato (dry-run)",
};

export const REASON_HELP: Record<string, string> = {
  insufficient_evidence: "Nessuna evidenza sufficiente", low_confidence: "Confidenza bassa", pii_detected: "Possibili dati personali", banned_words: "Parole vietate",
  render_invalid: "Render non valido", overlay_clipped: "Overlay tagliato", crop_warning: "Crop dubbio", duplicate_suspect: "Titolo duplicato",
  unsupported_numbers: "Numeri non supportati", title_not_grounded: "Titolo non ancorato alla scena", simulated_analysis: "Analisi simulata",
  analysis_failed: "Analisi AI non riuscita", transcript_unusable: "Trascrizione inutilizzabile", asr_unavailable: "Trascrizione non disponibile",
  sensitive_terms: "Possibile contenuto sensibile", caption_malformed: "Caption malformata", caption_has_url: "URL nella caption",
  overlay_outside_safe_area: "Overlay fuori safe area", av_desync: "Audio/video non sincronizzati",
};

/** Stato remoto granulare di un job: «Buffer dice inviato» non equivale a «verificato su TikTok». */
export const REMOTE_STATE_LABEL: Record<string, { label: string; tone: "zinc" | "violet" | "amber" | "green" | "red" | "blue"; help: string }> = {
  LOCAL: { label: "Solo locale", tone: "zinc", help: "Nulla è stato inviato a Buffer." },
  SENDING: { label: "In invio", tone: "amber", help: "Richiesta in corso o esito da riconciliare: nessun secondo invio finché non è chiaro." },
  SCHEDULED_ON_BUFFER: { label: "Programmato su Buffer", tone: "violet", help: "Buffer ha accettato il post; non è ancora stato pubblicato." },
  BUFFER_REPORTS_SENT: { label: "Buffer segnala inviato", tone: "blue", help: "Conferma generica del provider: la visibilità su TikTok NON è verificata." },
  BUFFER_REPORTS_ERROR: { label: "Buffer segnala errore", tone: "red", help: "Buffer riporta un errore di pubblicazione." },
  EXTERNALLY_VERIFIED: { label: "Verificato su TikTok", tone: "green", help: "Confermato manualmente dal proprietario." },
  UNCERTAIN: { label: "Stato esterno incerto", tone: "amber", help: "Lo stato remoto non è determinabile: controlla su Buffer/TikTok." },
};

export const DIAG_STATE: Record<string, { label: string; tone: "zinc" | "violet" | "amber" | "green" | "red" | "blue" }> = {
  NOT_CONFIGURED: { label: "Non configurato", tone: "zinc" },
  CONFIGURED: { label: "Configurato", tone: "blue" },
  CONNECTED: { label: "Connesso", tone: "green" },
  TEST_FAILED: { label: "Test fallito", tone: "red" },
  NEEDS_LIVE_VERIFICATION: { label: "Da verificare live", tone: "amber" },
};
