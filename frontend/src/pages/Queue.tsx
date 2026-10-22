import { useQuery } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Ban, Pause, Play, Send } from "lucide-react";
import { useState } from "react";
import { Badge, Button, Card, EmptyState, ErrorState, Field, Modal, PageHeader, SimBadge, Spinner, StatusBadge } from "../components/ui";
import { useAction } from "../hooks/useApi";
import { get, patch, post } from "../lib/api";
import { dayKey, fmtDay, fmtTime } from "../lib/format";
import type { Job } from "../types";

interface QueueData { items: Job[]; daily_counts: Record<string, number>; daily_max: number; slots: string[]; timezone: string; publish_mode: string }

function Row({ j, prev, next }: { j: Job; prev?: Job; next?: Job }) {
  const [edit, setEdit] = useState(false);
  const [cap, setCap] = useState(j.caption);
  const [tags, setTags] = useState(j.hashtags.join(" "));
  const local = ["QUEUED_LOCAL", "RETRY_PENDING"].includes(j.status);
  const inv = [["queue"], ["status"], ["history"]];
  const swap = useAction((o: string) => patch(`/api/v1/queue/${j.id}`, { swap_with: o }), { invalidate: inv });
  const pause = useAction(() => patch(`/api/v1/queue/${j.id}`, { paused: !j.paused }), { ok: j.paused ? "Ripreso" : "In pausa", invalidate: inv });
  const cancel = useAction(() => patch(`/api/v1/queue/${j.id}`, { cancel: true }), { ok: "Annullato e ripianificato", invalidate: inv });
  const dispatch = useAction(() => post(`/api/v1/publications/${j.id}/dispatch`), { ok: (r: any) => `Esito: ${r.status}`, invalidate: inv });
  const save = useAction(() => patch(`/api/v1/queue/${j.id}`, { caption: cap, hashtags: tags.split(/\s+/).filter(Boolean) }), { ok: "Caption aggiornata", invalidate: inv });
  return (
    <li className="flex flex-wrap items-center gap-3 rounded-lg border border-zinc-800 bg-zinc-900/40 p-3">
      <span className="w-14 text-lg font-semibold tabular-nums">{fmtTime(j.due_at)}</span>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium">{j.overlay_title ?? j.clip_title}</p>
        <p className="truncate text-xs text-zinc-500">{j.caption}</p>
        {j.error && <p className="text-xs text-red-300">{j.error}</p>}
      </div>
      <div className="flex flex-wrap items-center gap-1">
        <StatusBadge status={j.status} />
        {j.paused && <Badge tone="amber">In pausa</Badge>}
        {j.simulated && <SimBadge />}
        <Badge tone={["SCHEDULED_REMOTE", "PUBLISHED"].includes(j.status) ? "violet" : "zinc"}>{["SCHEDULED_REMOTE", "PUBLISHED"].includes(j.status) ? "REMOTE" : "LOCAL"}</Badge>
      </div>
      <div className="flex gap-1">
        {local && <Button variant="ghost" aria-label="Sposta su" disabled={!prev || !["QUEUED_LOCAL", "RETRY_PENDING"].includes(prev.status)} onClick={() => prev && swap.mutate(prev.id)}><ArrowUp className="h-3.5 w-3.5" /></Button>}
        {local && <Button variant="ghost" aria-label="Sposta giù" disabled={!next || !["QUEUED_LOCAL", "RETRY_PENDING"].includes(next.status)} onClick={() => next && swap.mutate(next.id)}><ArrowDown className="h-3.5 w-3.5" /></Button>}
        {local && <Button variant="ghost" onClick={() => pause.mutate()}>{j.paused ? <Play className="h-3.5 w-3.5" /> : <Pause className="h-3.5 w-3.5" />}</Button>}
        {local && <Button variant="ghost" onClick={() => setEdit(true)}>Caption</Button>}
        {local && <Button variant="ghost" busy={dispatch.isPending} onClick={() => dispatch.mutate()}><Send className="h-3.5 w-3.5" />Invia ora</Button>}
        {local && <Button variant="ghost" onClick={() => cancel.mutate()}><Ban className="h-3.5 w-3.5" /></Button>}
      </div>
      {edit && (
        <Modal title="Modifica caption" onClose={() => setEdit(false)}>
          <div className="space-y-3">
            <Field label="Caption"><textarea className="input h-28" value={cap} onChange={(e) => setCap(e.target.value)} /></Field>
            <Field label="Hashtag"><input className="input" value={tags} onChange={(e) => setTags(e.target.value)} /></Field>
            <div className="flex justify-end"><Button busy={save.isPending} onClick={() => save.mutate(undefined, { onSuccess: () => setEdit(false) })}>Salva</Button></div>
          </div>
        </Modal>
      )}
    </li>
  );
}

export function QueuePage() {
  const q = useQuery({ queryKey: ["queue"], queryFn: () => get<QueueData>("/api/v1/queue"), refetchInterval: 4000 });
  const rebuild = useAction(() => post("/api/v1/queue/rebuild"), { ok: "Coda ricostruita", invalidate: [["queue"]] });
  const tick = useAction(() => post("/api/v1/queue/tick"), { ok: (r: any) => `Publisher: ${r.processed} inviati, ${r.reconciled} riconciliati`, invalidate: [["queue"], ["status"]] });
  if (q.isLoading) return <Spinner />;
  if (q.error) return <ErrorState error={q.error} retry={() => q.refetch()} />;
  const d = q.data!;
  const groups = new Map<string, Job[]>();
  d.items.forEach((j) => { const k = dayKey(j.due_at); groups.set(k, [...(groups.get(k) ?? []), j]); });
  return (
    <>
      <PageHeader title="Coda e calendario" subtitle={`Slot ${d.slots.join(" · ")} (${d.timezone}) · max ${d.daily_max}/giorno · FIFO tra clip idonee`}
        actions={<><Button variant="ghost" busy={rebuild.isPending} onClick={() => rebuild.mutate()}>Ricostruisci coda</Button><Button busy={tick.isPending} onClick={() => tick.mutate()}>Esegui publisher ora</Button></>} />
      {d.publish_mode !== "live" && <div className="mb-4 rounded-xl border border-amber-500/30 bg-amber-500/10 p-3 text-sm text-amber-100">{d.publish_mode === "mock" ? "Publisher DEMO: il ciclo di vita remoto è simulato." : "DRY RUN attivo: i job risultano «Simulato» e non viene inviata nessuna richiesta esterna."}</div>}
      {!d.items.length ? <EmptyState title="Coda vuota" hint="Le clip AUTO_READY o APPROVED entrano qui automaticamente. Le clip in revisione non occupano slot." /> : (
        <div className="space-y-4">
          {[...groups.entries()].map(([k, jobs]) => (
            <Card key={k} title={<span className="capitalize">{fmtDay(jobs[0].due_at)}</span>} actions={<Badge tone={(d.daily_counts[k] ?? 0) >= d.daily_max ? "amber" : "zinc"}>{d.daily_counts[k] ?? 0}/{d.daily_max} post</Badge>}>
              <ul className="space-y-2">{jobs.map((j, i) => <Row key={j.id} j={j} prev={jobs[i - 1]} next={jobs[i + 1]} />)}</ul>
            </Card>
          ))}
        </div>
      )}
    </>
  );
}
