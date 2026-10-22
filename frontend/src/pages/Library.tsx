import { useQuery } from "@tanstack/react-query";
import { ExternalLink, ListPlus, Pencil, RefreshCcw, SkipForward, Sparkles, Video as VideoIcon } from "lucide-react";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Thumb, Video } from "../components/ClipMedia";
import { Badge, Button, Card, EmptyState, ErrorState, Field, Modal, PageHeader, SimBadge, Spinner, StatusBadge } from "../components/ui";
import { useAction } from "../hooks/useApi";
import { get, patch, post } from "../lib/api";
import { fmtDateTime, fmtDur, REASON_HELP } from "../lib/format";
import type { Clip } from "../types";

const STATUSES = ["", "DISCOVERED", "WAITING_FOR_MEDIA", "DOWNLOADED", "ANALYZED", "RENDERING", "AUTO_READY", "NEEDS_REVIEW", "APPROVED", "REJECTED", "SKIPPED", "FAILED"];

function EditTitle({ clip, onClose }: { clip: Clip; onClose: () => void }) {
  const [t, setT] = useState(clip.overlay_title ?? "");
  const [cap, setCap] = useState(clip.caption ?? "");
  const [tags, setTags] = useState((clip.hashtags ?? []).join(" "));
  const save = useAction(() => patch(`/api/v1/reviews/${clip.id}/content`, { overlay_title: t, caption: cap, hashtags: tags.split(/\s+/).filter(Boolean) }), { ok: "Salvato: render in corso", invalidate: [["clips"], ["status"]] });
  return (
    <Modal title="Modifica testi" onClose={onClose}>
      <div className="space-y-3">
        <Field label="Titolo overlay"><input className="input" value={t} onChange={(e) => setT(e.target.value)} maxLength={120} /></Field>
        <Field label="Caption TikTok"><textarea className="input h-24" value={cap} onChange={(e) => setCap(e.target.value)} /></Field>
        <Field label="Hashtag (max 5)"><input className="input" value={tags} onChange={(e) => setTags(e.target.value)} /></Field>
        <div className="flex justify-end"><Button busy={save.isPending} onClick={() => save.mutate(undefined, { onSuccess: onClose })}>Salva e ri-renderizza</Button></div>
      </div>
    </Modal>
  );
}

function Detail({ id, onClose }: { id: string; onClose: () => void }) {
  const q = useQuery({ queryKey: ["clip", id], queryFn: () => get<any>(`/api/v1/clips/${id}`), refetchInterval: 4000 });
  const d = q.data;
  return (
    <Modal title="Dettaglio clip" onClose={onClose}>
      {q.isLoading ? <Spinner /> : q.error ? <ErrorState error={q.error} /> : (
        <div className="space-y-3 text-sm">
          <div className="grid grid-cols-2 gap-2">
            <div><div className="label">Originale</div>{d.has_original ? <Video id={id} kind="original" className="w-full" /> : <p className="text-zinc-500">Non scaricato</p>}</div>
            <div><div className="label">Render</div>{d.has_render ? <Video id={id} kind="render" className="w-full" /> : <p className="text-zinc-500">Non ancora renderizzato</p>}</div>
          </div>
          <div><span className="label">Titolo Twitch</span>{d.title} <span className="text-zinc-500">({d.game_name} · {d.creator_name})</span></div>
          <div><span className="label">Overlay</span>{d.overlay_title ?? "—"}</div>
          <div><span className="label">Caption</span>{d.caption ?? "—"} <span className="text-zinc-400">{(d.hashtags ?? []).join(" ")}</span></div>
          <div><span className="label">Trascrizione</span>{d.analyses?.[0]?.transcript || <i className="text-zinc-500">nessun parlato rilevato</i>} {d.analyses?.[0]?.simulated && <SimBadge />}</div>
          {d.error && <div className="rounded-lg bg-red-500/10 p-2 text-red-200">{d.error}</div>}
          {d.review_reasons?.length > 0 && <ul className="list-disc pl-5 text-amber-200">{d.review_reasons.map((r: any) => <li key={r.code}>{r.message}</li>)}</ul>}
          <div><span className="label">Cronologia stati</span>
            <ul className="max-h-40 space-y-0.5 overflow-auto text-xs text-zinc-400">{d.transitions.map((t: any, i: number) => <li key={i}>{fmtDateTime(t.at)} · {t.from ?? "∅"} → <b>{t.to}</b> {t.note && `· ${t.note}`}</li>)}</ul>
          </div>
        </div>
      )}
    </Modal>
  );
}

export function Library() {
  const [params] = useSearchParams();
  const [q, setQ] = useState("");
  const [status, setStatus] = useState(params.get("status") ?? "");
  const [page, setPage] = useState(1);
  const [edit, setEdit] = useState<Clip | null>(null);
  const [detail, setDetail] = useState<string | null>(null);
  const list = useQuery({
    queryKey: ["clips", q, status, page],
    queryFn: () => get<{ items: Clip[]; total: number; page_size: number }>(`/api/v1/clips?page=${page}&page_size=18&q=${encodeURIComponent(q)}${status ? `&status=${status}` : ""}`),
    refetchInterval: 4000,
    placeholderData: (p) => p,
  });
  const run = (path: string, ok: string) => useAction((id: string) => post(`/api/v1/clips/${id}/${path}`), { ok }); // eslint-disable-line react-hooks/rules-of-hooks
  const analyze = run("analyze", "Analisi avviata");
  const render = run("render", "Render avviato");
  const retry = run("retry", "Retry avviato");
  const skip = run("skip", "Clip saltata");
  const enqueue = run("queue", "Aggiunta alla coda");
  const totalPages = Math.max(1, Math.ceil((list.data?.total ?? 0) / 18));

  return (
    <>
      <PageHeader title="Clip Library" subtitle={`${list.data?.total ?? 0} clip`} />
      <div className="mb-4 flex flex-wrap gap-2">
        <input className="input max-w-xs" placeholder="Cerca titolo, gioco, creator…" value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} />
        <select className="input max-w-[200px]" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
          {STATUSES.map((s) => <option key={s} value={s}>{s || "Tutti gli stati"}</option>)}
        </select>
      </div>
      {list.isLoading ? <Spinner /> : list.error ? <ErrorState error={list.error} retry={() => list.refetch()} /> : !list.data?.items.length ? (
        <EmptyState title="Nessuna clip" hint="Premi «Run once» nell'Overview per importare le clip delle ultime 24 ore." />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {list.data.items.map((c) => (
            <Card key={c.id} className="flex flex-col gap-3">
              <div className="flex gap-3">
                <button onClick={() => setDetail(c.id)} className="w-24 shrink-0"><Thumb id={c.id} kind={c.has_render ? "render" : "original"} className="aspect-[9/16] w-full rounded-lg" /></button>
                <div className="min-w-0 flex-1 space-y-1.5 text-sm">
                  <div className="flex flex-wrap gap-1"><StatusBadge status={c.status} />{c.is_portrait_file ? <Badge tone="green">Portrait</Badge> : c.has_original ? <Badge tone="amber" title="Convertita da landscape">Landscape→9:16</Badge> : c.portrait_available === false ? <Badge tone="amber">Portrait assente</Badge> : null}{c.is_demo && <SimBadge />}</div>
                  <p className="truncate font-medium" title={c.title}>{c.title}</p>
                  <p className="text-xs text-zinc-500">{c.game_name} · {c.creator_name} · {fmtDur(c.duration)} · {fmtDateTime(c.created_at_utc)}</p>
                  {c.overlay_title && <p className="text-xs text-violet-300">«{c.overlay_title}»</p>}
                  {c.transcript_excerpt && <p className="line-clamp-2 text-xs text-zinc-400">{c.transcript_excerpt}</p>}
                  {c.error && <p className="line-clamp-2 text-xs text-red-300">{c.error}</p>}
                  {c.review_reasons.length > 0 && c.status === "NEEDS_REVIEW" && <p className="text-xs text-amber-300">{c.review_reasons.map((r) => REASON_HELP[r.code] ?? r.code).join(" · ")}</p>}
                </div>
              </div>
              <div className="flex flex-wrap gap-1.5">
                <Button variant="ghost" disabled={!c.has_original} onClick={() => analyze.mutate(c.id)}><Sparkles className="h-3.5 w-3.5" />Analyze</Button>
                <Button variant="ghost" disabled={!c.overlay_title} onClick={() => render.mutate(c.id)}><VideoIcon className="h-3.5 w-3.5" />Render</Button>
                <Button variant="ghost" disabled={!c.overlay_title} onClick={() => setEdit(c)}><Pencil className="h-3.5 w-3.5" />Titolo</Button>
                {c.status === "FAILED" && <Button variant="ghost" onClick={() => retry.mutate(c.id)}><RefreshCcw className="h-3.5 w-3.5" />Retry</Button>}
                {["AUTO_READY", "APPROVED", "NEEDS_REVIEW", "SKIPPED", "REJECTED"].includes(c.status) && c.has_render && !["QUEUED_LOCAL", "SCHEDULED_REMOTE", "PUBLISHED", "DRY_RUN_OK"].includes(c.publication_status ?? "") && <Button variant="ghost" onClick={() => enqueue.mutate(c.id)}><ListPlus className="h-3.5 w-3.5" />Coda</Button>}
                {!["SKIPPED", "REJECTED"].includes(c.status) && <Button variant="ghost" onClick={() => skip.mutate(c.id)}><SkipForward className="h-3.5 w-3.5" />Skip</Button>}
                <a className="inline-flex items-center gap-1 rounded-lg border border-zinc-700 px-3 py-1.5 text-sm text-zinc-200 hover:bg-zinc-800" href={c.twitch_url} target="_blank" rel="noreferrer"><ExternalLink className="h-3.5 w-3.5" />Twitch</a>
              </div>
            </Card>
          ))}
        </div>
      )}
      <div className="mt-4 flex items-center justify-center gap-3 text-sm">
        <Button variant="ghost" disabled={page <= 1} onClick={() => setPage(page - 1)}>←</Button>
        <span className="text-zinc-400">{page} / {totalPages}</span>
        <Button variant="ghost" disabled={page >= totalPages} onClick={() => setPage(page + 1)}>→</Button>
      </div>
      {edit && <EditTitle clip={edit} onClose={() => setEdit(null)} />}
      {detail && <Detail id={detail} onClose={() => setDetail(null)} />}
    </>
  );
}
