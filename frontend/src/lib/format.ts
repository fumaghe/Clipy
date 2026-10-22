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
  SCHEDULED_REMOTE: "Pianificato (remoto)", PUBLISHED: "Pubblicato", RETRY_PENDING: "Nuovo tentativo", CANCELLED: "Annullato", DRY_RUN_OK: "Simulato (dry-run)",
};

export const REASON_HELP: Record<string, string> = {
  insufficient_evidence: "Nessuna evidenza sufficiente", low_confidence: "Confidenza bassa", pii_detected: "Possibili dati personali", banned_words: "Parole vietate",
  render_invalid: "Render non valido", overlay_clipped: "Overlay tagliato", crop_warning: "Crop dubbio", duplicate_suspect: "Titolo duplicato",
  unsupported_numbers: "Numeri non supportati", title_not_grounded: "Titolo non ancorato alla scena", simulated_analysis: "Analisi simulata",
};
