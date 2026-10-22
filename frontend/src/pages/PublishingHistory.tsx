import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Badge, Button, EmptyState, ErrorState, PageHeader, RemoteStateBadge, SimBadge, Spinner, StatusBadge } from "../components/ui";
import { useAction } from "../hooks/useApi";
import { get, post } from "../lib/api";
import { fmtDateTime } from "../lib/format";
import type { Job } from "../types";

export function PublishingHistory() {
  const [status, setStatus] = useState("");
  const q = useQuery({ queryKey: ["history", status], queryFn: () => get<{ items: Job[] }>(`/api/v1/publications${status ? `?status=${status}` : ""}`), refetchInterval: 5000 });
  const reconcile = useAction((id: string) => post(`/api/v1/publications/${id}/reconcile`), { ok: "Riconciliato con lo stato remoto", invalidate: [["history"], ["queue"]] });
  const verify = useAction((id: string) => post(`/api/v1/publications/${id}/verify-external`, { note: "verificato dall'utente su TikTok" }), { ok: "Segnato come verificato su TikTok", invalidate: [["history"]] });
  const retry = useAction((id: string) => post(`/api/v1/publications/${id}/retry`), { ok: "Rimesso in coda", invalidate: [["history"], ["queue"]] });
  return (
    <>
      <PageHeader title="Storico pubblicazioni" subtitle="«Inviato (Buffer)» = Buffer lo segnala come inviato, NON verificato su TikTok. «Verificato su TikTok» = conferma manuale. «Simulato» = dry-run/demo, nessun post reale." />
      <select className="input mb-4 max-w-xs" value={status} onChange={(e) => setStatus(e.target.value)}>
        <option value="">Tutti gli stati</option>
        {["PUBLISHED", "SCHEDULED_REMOTE", "DRY_RUN_OK", "FAILED", "NEEDS_REVIEW", "RETRY_PENDING", "CANCELLED"].map((s) => <option key={s}>{s}</option>)}
      </select>
      {q.isLoading ? <Spinner /> : q.error ? <ErrorState error={q.error} retry={() => q.refetch()} /> : !q.data?.items.length ? <EmptyState title="Nessuna pubblicazione" hint="Quando i job vengono inviati (o simulati) compaiono qui." /> : (
        <div className="overflow-x-auto rounded-xl border border-zinc-800">
          <table className="w-full text-left text-sm">
            <thead className="bg-zinc-900 text-xs uppercase text-zinc-400"><tr><th className="p-3">Slot</th><th>Clip</th><th>Stato</th><th>Remoto</th><th>Tentativi</th><th>Errore</th><th></th></tr></thead>
            <tbody className="divide-y divide-zinc-800">
              {q.data.items.map((j) => (
                <tr key={j.id}>
                  <td className="whitespace-nowrap p-3">{fmtDateTime(j.due_at)}</td>
                  <td className="max-w-[260px] truncate">{j.overlay_title ?? j.clip_title}</td>
                  <td><div className="flex flex-wrap gap-1"><StatusBadge status={j.status} />{j.simulated && <SimBadge />}<RemoteStateBadge state={j.simulated ? undefined : j.remote_state} /></div></td>
                  <td className="text-xs text-zinc-400">{j.remote_post_id ?? "—"} {j.remote_status && <Badge>{j.remote_status}</Badge>} {j.remote_url ? <a className="text-brand-400 underline" href={j.remote_url} target="_blank" rel="noreferrer">link</a> : null}</td>
                  <td>{j.attempts}</td>
                  <td className="max-w-[240px] truncate text-xs text-red-300" title={j.error ?? ""}>{j.error}</td>
                  <td className="space-x-1 whitespace-nowrap p-2">
                    {j.remote_post_id && ["SCHEDULED_REMOTE", "NEEDS_REVIEW", "FAILED"].includes(j.status) && <Button variant="ghost" onClick={() => reconcile.mutate(j.id)}>Riconcilia</Button>}
                    {j.status === "PUBLISHED" && !j.simulated && j.verification !== "EXTERNALLY_VERIFIED" && <Button variant="ghost" title="Conferma di aver visto il video sul profilo TikTok" onClick={() => verify.mutate(j.id)}>Ho verificato su TikTok</Button>}
                    {["FAILED", "NEEDS_REVIEW", "DRY_RUN_OK", "CANCELLED"].includes(j.status) && <Button variant="ghost" onClick={() => retry.mutate(j.id)}>Riprova</Button>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
