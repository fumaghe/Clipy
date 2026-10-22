import { useState } from "react";

export function Thumb({ id, kind = "render", className = "" }: { id: string; kind?: "render" | "original"; className?: string }) {
  const [failed, setFailed] = useState(false);
  if (failed) return <div className={`flex items-center justify-center bg-zinc-800 text-xs text-zinc-500 ${className}`}>nessuna anteprima</div>;
  return <img loading="lazy" alt="anteprima clip" src={`/api/v1/clips/${id}/preview?kind=${kind}`} onError={() => setFailed(true)} className={`object-cover ${className}`} />;
}

export function Video({ id, kind, className = "" }: { id: string; kind: "render" | "original"; className?: string }) {
  return <video controls preload="metadata" src={`/api/v1/clips/${id}/video?kind=${kind}`} className={`rounded-lg bg-black ${className}`} />;
}
