import { Play } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { Clip } from "../types";

export type MediaClip = Pick<Clip, "id" | "status" | "has_original" | "has_render" | "content_version" | "busy_reason">;

/** Quale file ha senso richiedere ora (null = nessuno è ancora stato generato: niente richieste, solo placeholder). */
export function mediaKind(c: Pick<Clip, "has_original" | "has_render">): "render" | "original" | null {
  return c.has_render ? "render" : c.has_original ? "original" : null;
}

const PENDING_LABEL: Record<string, string> = {
  DISCOVERED: "In attesa di download",
  WAITING_FOR_MEDIA: "Twitch sta preparando il file",
  DOWNLOADED: "Download completato",
  ANALYZING: "Analisi in corso…",
  ANALYZED: "In attesa del render",
  RENDERING: "Render in corso…",
  READY: "Render in corso…",
  FAILED: "File non disponibile",
};

export function mediaPlaceholder(c: Pick<Clip, "status" | "busy_reason">): string {
  return c.busy_reason ?? PENDING_LABEL[c.status] ?? "Nessuna anteprima";
}

const mediaUrl = (id: string, what: "video" | "preview", kind: string, version?: number) =>
  `/api/v1/clips/${id}/${what}?kind=${kind}${version != null ? `&v=${version}` : ""}`;

/** Anteprima: non chiede mai un file che non esiste ancora (niente 404 «normali»); il `v=` invalida la cache quando il contenuto cambia. */
export function Thumb({ clip, className = "" }: { clip: MediaClip; className?: string }) {
  const kind = mediaKind(clip);
  const src = kind ? mediaUrl(clip.id, "preview", kind, clip.content_version) : null;
  const [failedSrc, setFailedSrc] = useState<string | null>(null);
  if (!src || failedSrc === src) {
    const label = src ? "Anteprima non disponibile" : mediaPlaceholder(clip);
    return <div role="img" aria-label={label} className={`flex items-center justify-center bg-zinc-800 p-1 text-center text-xs text-zinc-500 ${className}`}>{label}</div>;
  }
  return (
    <span className={`relative block overflow-hidden ${className}`}>
      <img loading="lazy" alt="anteprima clip" src={src} onError={() => setFailedSrc(src)} className="h-full w-full object-cover" />
      {clip.busy_reason && <span className="absolute inset-x-0 bottom-0 bg-black/70 px-1 py-0.5 text-center text-[10px] text-amber-200">{clip.busy_reason}</span>}
    </span>
  );
}

/** Player a richiesta: il <video> (e quindi le richieste HTTP Range) nasce solo al click, così decine di card non aprono
 *  decine di connessioni che il browser poi annulla. Allo smontaggio rilascia esplicitamente lo stream. */
export function Video({ id, kind, version, className = "" }: { id: string; kind: "render" | "original"; version?: number; className?: string }) {
  const [active, setActive] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const ref = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    const el = ref.current;
    return () => {
      if (el) {
        el.pause();
        el.removeAttribute("src");
        el.load();
      }
    };
  }, [active]);
  if (!active) {
    return (
      <button type="button" aria-label={`Riproduci ${kind === "render" ? "render" : "originale"}`} onClick={() => { setErr(null); setActive(true); }}
        className={`group relative block overflow-hidden rounded-lg bg-black ${className}`}>
        <img loading="lazy" alt="" src={mediaUrl(id, "preview", kind, version)} className="h-full w-full object-contain" onError={(e) => { e.currentTarget.style.display = "none"; }} />
        <span className="absolute inset-0 flex items-center justify-center bg-black/30 text-white transition group-hover:bg-black/10"><Play className="h-8 w-8" /></span>
      </button>
    );
  }
  return (
    <div>
      <video ref={ref} controls autoPlay preload="metadata" src={mediaUrl(id, "video", kind, version)} className={`rounded-lg bg-black ${className}`}
        onError={(e) => { if (e.currentTarget.error?.code !== MediaError.MEDIA_ERR_ABORTED) setErr("Impossibile riprodurre il video"); }} />
      {err && <p className="mt-1 text-xs text-red-300">{err} · <button type="button" className="underline" onClick={() => { setErr(null); setActive(false); }}>riprova</button></p>}
    </div>
  );
}
