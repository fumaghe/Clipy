import { AlertTriangle, Inbox, Loader2, X } from "lucide-react";
import { createContext, useCallback, useContext, useState, type ButtonHTMLAttributes, type ReactNode } from "react";
import { REMOTE_STATE_LABEL, STATUS_LABEL } from "../lib/format";

export function Button({ variant = "primary", busy, className = "", children, ...rest }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "ghost" | "danger" | "success"; busy?: boolean }) {
  const styles = {
    primary: "bg-brand-600 hover:bg-brand-500 text-white",
    ghost: "border border-zinc-700 hover:bg-zinc-800 text-zinc-200",
    danger: "bg-red-600/90 hover:bg-red-500 text-white",
    success: "bg-emerald-600 hover:bg-emerald-500 text-white",
  }[variant];
  return (
    <button {...rest} disabled={rest.disabled || busy} className={`inline-flex items-center justify-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${styles} ${className}`}>
      {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
      {children}
    </button>
  );
}

const TONE: Record<string, string> = {
  green: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  amber: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  red: "bg-red-500/15 text-red-300 border-red-500/30",
  violet: "bg-violet-500/15 text-violet-300 border-violet-500/30",
  zinc: "bg-zinc-800 text-zinc-300 border-zinc-700",
  blue: "bg-sky-500/15 text-sky-300 border-sky-500/30",
};

export function Badge({ tone = "zinc", children, title }: { tone?: keyof typeof TONE; children: ReactNode; title?: string }) {
  return <span title={title} className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium ${TONE[tone]}`}>{children}</span>;
}

export function statusTone(s: string): keyof typeof TONE {
  if (["AUTO_READY", "APPROVED", "PUBLISHED"].includes(s)) return "green";
  if (["NEEDS_REVIEW", "WAITING_FOR_MEDIA", "RETRY_PENDING", "SCHEDULING"].includes(s)) return "amber";
  if (["FAILED", "REJECTED"].includes(s)) return "red";
  if (["SCHEDULED_REMOTE", "QUEUED_LOCAL", "UPLOADING_ASSET"].includes(s)) return "violet";
  if (s === "DRY_RUN_OK") return "blue";
  return "zinc";
}

export const StatusBadge = ({ status }: { status: string }) => <Badge tone={statusTone(status)}>{STATUS_LABEL[status] ?? status}</Badge>;

export function RemoteStateBadge({ state }: { state?: string }) {
  if (!state || state === "LOCAL") return null;
  const m = REMOTE_STATE_LABEL[state];
  return m ? <Badge tone={m.tone} title={m.help}>{m.label}</Badge> : <Badge>{state}</Badge>;
}

export const SimBadge = ({ label = "SIMULATO" }: { label?: string }) => <Badge tone="blue" title="Dato/azione simulata in modalità demo">{label}</Badge>;

export function Card({ title, actions, children, className = "" }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`card p-4 ${className}`}>
      {(title || actions) && (
        <header className="mb-3 flex items-center justify-between gap-2">
          <h2 className="text-sm font-semibold text-zinc-200">{title}</h2>
          {actions}
        </header>
      )}
      {children}
    </section>
  );
}

export const Spinner = ({ label = "Caricamento…" }: { label?: string }) => (
  <div role="status" className="flex items-center justify-center gap-2 p-10 text-sm text-zinc-400"><Loader2 className="h-4 w-4 animate-spin" />{label}</div>
);

export const EmptyState = ({ title, hint, action }: { title: string; hint?: string; action?: ReactNode }) => (
  <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed border-zinc-800 p-10 text-center">
    <Inbox className="h-8 w-8 text-zinc-600" />
    <p className="text-sm font-medium text-zinc-300">{title}</p>
    {hint && <p className="max-w-md text-xs text-zinc-500">{hint}</p>}
    {action}
  </div>
);

export const ErrorState = ({ error, retry }: { error: unknown; retry?: () => void }) => (
  <div role="alert" className="flex items-start gap-3 rounded-xl border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-200">
    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
    <div className="flex-1">{error instanceof Error ? error.message : "Errore sconosciuto"}</div>
    {retry && <Button variant="ghost" onClick={retry}>Riprova</Button>}
  </div>
);

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold text-white">{title}</h1>
        {subtitle && <p className="text-sm text-zinc-400">{subtitle}</p>}
      </div>
      <div className="flex flex-wrap gap-2">{actions}</div>
    </div>
  );
}

export function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4" onClick={onClose}>
      <div role="dialog" aria-label={title} className="max-h-[90vh] w-full max-w-lg overflow-auto rounded-xl border border-zinc-700 bg-zinc-900 p-5" onClick={(e) => e.stopPropagation()}>
        <div className="mb-3 flex items-center justify-between"><h3 className="font-semibold">{title}</h3><button aria-label="Chiudi" onClick={onClose}><X className="h-4 w-4" /></button></div>
        {children}
      </div>
    </div>
  );
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="block">
      <span className="label">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-zinc-500">{hint}</span>}
    </label>
  );
}

// ---- toast
type Toast = { id: number; text: string; tone: "ok" | "err" };
const ToastCtx = createContext<(text: string, tone?: "ok" | "err") => void>(() => {});
export const useToast = () => useContext(ToastCtx);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const push = useCallback((text: string, tone: "ok" | "err" = "ok") => {
    const id = Date.now() + Math.random();
    setItems((x) => [...x, { id, text, tone }]);
    setTimeout(() => setItems((x) => x.filter((t) => t.id !== id)), 5000);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="fixed bottom-4 right-4 z-[60] flex max-w-sm flex-col gap-2" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className={`rounded-lg border px-3 py-2 text-sm shadow-lg ${t.tone === "ok" ? "border-emerald-500/40 bg-emerald-950 text-emerald-100" : "border-red-500/40 bg-red-950 text-red-100"}`}>{t.text}</div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}
