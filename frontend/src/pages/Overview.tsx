import { useQuery } from "@tanstack/react-query";
import { Download, Play, RefreshCw, Square, Wand2 } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Button, Card, EmptyState, ErrorState, Field, Modal, PageHeader, SimBadge, Spinner, StatusBadge } from "../components/ui";
import { Thumb } from "../components/ClipMedia";
import { useAction, useStatus } from "../hooks/useApi";
import { get, post } from "../lib/api";
import { fmtDateTime } from "../lib/format";
import type { Clip, EventRow, Job } from "../types";

function Kpi({ label, value, tone, to }: { label: string; value: number; tone?: string; to?: string }) {
  const body = (
    <div className="card p-4">
      <div className={`text-3xl font-bold ${tone ?? "text-white"}`}>{value}</div>
      <div className="mt-1 text-xs uppercase tracking-wide text-zinc-400">{label}</div>
    </div>
  );
  return to ? <Link to={to}>{body}</Link> : body;
}

export function Overview() {
  const { data: s } = useStatus();
  const events = useQuery({ queryKey: ["events"], queryFn: () => get<EventRow[]>("/api/v1/system/events?limit=12"), refetchInterval: 5000 });
  const queue = useQuery({ queryKey: ["queue"], queryFn: () => get<{ items: Job[] }>("/api/v1/queue"), refetchInterval: 5000 });
  const recent = useQuery({ queryKey: ["clips", "recent"], queryFn: () => get<{ items: Clip[] }>("/api/v1/clips?page_size=6"), refetchInterval: 5000 });
  const [importOpen, setImportOpen] = useState(false);
  const [ref, setRef] = useState("");

  const toggle = useAction((on: boolean) => post(`/api/v1/system/collector/${on ? "start" : "stop"}`), { ok: "Collector aggiornato" });
  const runOnce = useAction(() => post("/api/v1/clips/sync"), { ok: (r: any) => `Scansione: ${r.found} clip trovate, ${r.new} nuove${r.simulated ? " (simulato)" : ""}` });
  const spawn = useAction(() => post("/api/v1/system/demo/spawn"), { ok: "Nuova clip demo creata (simulata): premi «Run once»" });
  const imp = useAction((r: string) => post("/api/v1/clips/import", { ref: r }), { ok: "Clip importata", });

  if (!s) return <Spinner />;
  const c = s.counts;
  return (
    <>
      <PageHeader
        title="Overview"
        subtitle={`Canale ${s.twitch.login} · limite ${s.daily_max} post/giorno`}
        actions={
          <>
            {s.collector_enabled ? (
              <Button variant="ghost" onClick={() => toggle.mutate(false)} busy={toggle.isPending}><Square className="h-3.5 w-3.5" />Stop collector</Button>
            ) : (
              <Button onClick={() => toggle.mutate(true)} busy={toggle.isPending}><Play className="h-3.5 w-3.5" />Start collector</Button>
            )}
            <Button variant="ghost" onClick={() => runOnce.mutate()} busy={runOnce.isPending}><RefreshCw className="h-3.5 w-3.5" />Run once</Button>
            <Button variant="ghost" onClick={() => setImportOpen(true)}><Download className="h-3.5 w-3.5" />Importa clip</Button>
            {s.demo_mode && <Button variant="ghost" onClick={() => spawn.mutate()} busy={spawn.isPending}><Wand2 className="h-3.5 w-3.5" />Simula nuova clip</Button>}
          </>
        }
      />
      {s.demo_mode && (
        <div className="mb-4 rounded-xl border border-sky-500/30 bg-sky-500/10 p-3 text-sm text-sky-100">
          <b>Modalità demo.</b> Twitch, AI e pubblicazione sono <SimBadge /> finché non colleghi gli account in <Link className="underline" to="/settings">Settings</Link>. Nessun post reale viene inviato.
        </div>
      )}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <Kpi label="Clip nuove" value={c.new ?? 0} />
        <Kpi label="Elaborate" value={(c.processed ?? 0) + (c.processing ?? 0)} to="/library" />
        <Kpi label="Da rivedere" value={c.needs_review ?? 0} tone="text-amber-300" to="/review" />
        <Kpi label="In coda" value={(c.queued ?? 0) + (c.remote_scheduled ?? 0) + (c.dry_run_ok ?? 0)} tone="text-violet-300" to="/queue" />
        <Kpi label="Pubblicate oggi" value={c.published_today ?? 0} tone="text-emerald-300" to="/history" />
      </div>
      {(c.failed ?? 0) > 0 && <div className="mt-3"><Link to="/library?status=FAILED"><Badge tone="red">{c.failed} clip in errore: apri la Library</Badge></Link></div>}

      <div className="mt-5 grid gap-4 lg:grid-cols-3">
        <Card title="Ultime clip" className="lg:col-span-2" actions={<Link className="text-xs text-brand-400" to="/library">Tutte →</Link>}>
          {recent.isLoading ? <Spinner /> : recent.error ? <ErrorState error={recent.error} retry={() => recent.refetch()} /> : !recent.data?.items.length ? (
            <EmptyState title="Nessuna clip ancora" hint="Avvia il collector o premi «Run once» per importare le clip delle ultime 24 ore." />
          ) : (
            <div className="grid grid-cols-3 gap-3 md:grid-cols-6">
              {recent.data.items.map((cl) => (
                <Link key={cl.id} to="/library" className="space-y-1">
                  <Thumb clip={cl} className="aspect-[9/16] w-full rounded-lg" />
                  <StatusBadge status={cl.status} />
                </Link>
              ))}
            </div>
          )}
        </Card>
        <Card title="Prossimi slot">
          {!queue.data?.items.length ? <p className="text-sm text-zinc-500">Nessun post in coda. Le clip idonee compaiono qui.</p> : (
            <ul className="space-y-2 text-sm">
              {queue.data.items.slice(0, 5).map((j) => (
                <li key={j.id} className="flex items-center justify-between gap-2">
                  <span className="truncate">{j.overlay_title ?? j.clip_title}</span>
                  <span className="flex shrink-0 items-center gap-1"><span className="text-xs text-zinc-400">{fmtDateTime(j.due_at)}</span><StatusBadge status={j.status} /></span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
      <Card title="Ultime attività" className="mt-4">
        {events.isLoading ? <Spinner /> : events.error ? <ErrorState error={events.error} /> : !events.data?.length ? <p className="text-sm text-zinc-500">Nessuna attività.</p> : (
          <ul className="divide-y divide-zinc-800 text-sm">
            {events.data.map((e) => (
              <li key={e.id} className="flex gap-3 py-1.5">
                <span className="w-28 shrink-0 text-xs text-zinc-500">{fmtDateTime(e.at)}</span>
                <Badge tone={e.severity === "error" ? "red" : e.severity === "warning" ? "amber" : "zinc"}>{e.component}</Badge>
                <span className="text-zinc-300">{e.message}</span>
              </li>
            ))}
          </ul>
        )}
      </Card>
      {importOpen && (
        <Modal title="Importa clip Twitch" onClose={() => setImportOpen(false)}>
          <Field label="URL o ID clip" hint="Viene usato solo l'ID: nessuna richiesta verso URL arbitrari.">
            <input className="input" value={ref} onChange={(e) => setRef(e.target.value)} placeholder="https://clips.twitch.tv/…" />
          </Field>
          <div className="mt-4 flex justify-end">
            <Button busy={imp.isPending} disabled={ref.length < 6} onClick={() => imp.mutate(ref, { onSuccess: () => { setImportOpen(false); setRef(""); } })}>Importa</Button>
          </div>
        </Modal>
      )}
    </>
  );
}
