import { useQuery } from "@tanstack/react-query";
import { Check, RefreshCw, Trash2 } from "lucide-react";
import { useState } from "react";
import { Video } from "../components/ClipMedia";
import { Badge, Button, Card, EmptyState, ErrorState, Field, PageHeader, SimBadge, Spinner } from "../components/ui";
import { useAction } from "../hooks/useApi";
import { get, patch, post } from "../lib/api";
import { REASON_HELP } from "../lib/format";
import type { ReviewItem } from "../types";

function ReviewCard({ item }: { item: ReviewItem }) {
  const [title, setTitle] = useState(item.overlay_title ?? "");
  const [caption, setCaption] = useState(item.caption ?? "");
  const [tags, setTags] = useState((item.hashtags ?? []).join(" "));
  const dirty = title !== (item.overlay_title ?? "") || caption !== (item.caption ?? "") || tags !== (item.hashtags ?? []).join(" ");
  const inv = [["reviews"], ["status"], ["clips"], ["queue"]];
  const approve = useAction(() => post(`/api/v1/reviews/${item.id}/approve`), { ok: "Approvata: entra in coda", invalidate: inv });
  const reject = useAction(() => post(`/api/v1/reviews/${item.id}/reject`), { ok: "Scartata (file e audit conservati)", invalidate: inv });
  const regen = useAction(() => post(`/api/v1/reviews/${item.id}/regenerate`), { ok: "Titolo rigenerato, render in corso", invalidate: inv });
  const save = useAction((approveAfter: boolean) => patch(`/api/v1/reviews/${item.id}/content`, { overlay_title: title, caption, hashtags: tags.split(/\s+/).filter(Boolean), approve: approveAfter }),
    { ok: "Modifiche salvate: ri-render in corso", invalidate: inv });
  const rerender = useAction(() => post(`/api/v1/clips/${item.id}/render`), { ok: "Render riavviato", invalidate: inv });
  return (
    <Card>
      <div className="grid gap-4 lg:grid-cols-[auto_1fr]">
        <div className="grid grid-cols-2 gap-2 lg:w-[360px]">
          <div><div className="label">Originale</div>{item.has_original ? <Video id={item.id} kind="original" className="aspect-[9/16] w-full object-contain" /> : <p className="text-xs text-zinc-500">n/d</p>}</div>
          <div><div className="label">Render finale</div>{item.has_render ? <Video id={item.id} kind="render" className="aspect-[9/16] w-full object-contain" /> : <p className="text-xs text-amber-300">Render in corso…</p>}</div>
        </div>
        <div className="space-y-3 text-sm">
          <div className="flex flex-wrap items-center gap-2"><Badge tone="amber">Da rivedere</Badge>{item.is_demo && <SimBadge />}<span className="text-zinc-500">{item.game_name} · {item.creator_name}</span></div>
          <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3">
            <div className="label text-amber-300">Perché è in revisione</div>
            <ul className="list-disc space-y-0.5 pl-5 text-amber-100">{(item.review?.reasons ?? []).map((r) => <li key={r.code}><b>{REASON_HELP[r.code] ?? r.code}</b> — {r.message}</li>)}</ul>
            <div className="mt-1 text-[11px] text-amber-200/60">policy {item.review?.policy_version}</div>
          </div>
          <div><span className="label">Trascrizione</span><p className="text-zinc-300">{item.transcript || <i className="text-zinc-500">nessun parlato rilevato</i>}</p></div>
          {(item.visual_notes?.length ?? 0) > 0 && <div><span className="label">Evidenze scena</span><p className="text-zinc-300">{item.visual_notes.join(" · ")}</p></div>}
          <div className="grid gap-2 md:grid-cols-2">
            <Field label="Titolo overlay"><input className="input" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={120} /></Field>
            <Field label="Hashtag"><input className="input" value={tags} onChange={(e) => setTags(e.target.value)} /></Field>
          </div>
          <Field label="Caption TikTok"><textarea className="input h-20" value={caption} onChange={(e) => setCaption(e.target.value)} /></Field>
          <div className="flex flex-wrap gap-2">
            <Button variant="success" busy={approve.isPending} disabled={!item.has_render || dirty} onClick={() => approve.mutate()}><Check className="h-3.5 w-3.5" />Approva</Button>
            <Button variant="ghost" busy={save.isPending} disabled={!dirty} onClick={() => save.mutate(true)}>Modifica e approva</Button>
            <Button variant="ghost" busy={save.isPending} disabled={!dirty} onClick={() => save.mutate(false)}>Salva modifiche</Button>
            <Button variant="ghost" busy={regen.isPending} onClick={() => regen.mutate()}><RefreshCw className="h-3.5 w-3.5" />Rigenera</Button>
            <Button variant="ghost" busy={rerender.isPending} onClick={() => rerender.mutate()}>Riprova render</Button>
            <Button variant="danger" busy={reject.isPending} onClick={() => reject.mutate()}><Trash2 className="h-3.5 w-3.5" />Scarta</Button>
          </div>
          {dirty && <p className="text-xs text-zinc-500">Salva le modifiche prima di approvare: l'approvazione riguarda il render attuale.</p>}
        </div>
      </div>
    </Card>
  );
}

export function ReviewInbox() {
  const q = useQuery({ queryKey: ["reviews"], queryFn: () => get<{ items: ReviewItem[] }>("/api/v1/reviews"), refetchInterval: 4000 });
  return (
    <>
      <PageHeader title="Review Inbox" subtitle="Solo le clip dubbie: le altre procedono da sole e non vengono bloccate da questa coda." />
      {q.isLoading ? <Spinner /> : q.error ? <ErrorState error={q.error} retry={() => q.refetch()} /> : !q.data?.items.length ? (
        <EmptyState title="Nessuna clip da rivedere" hint="Quando la review gate rileva incertezza (evidenze deboli, dati personali, crop dubbio…) la clip compare qui." />
      ) : (
        <div className="space-y-4">{q.data.items.map((i) => <ReviewCard key={`${i.id}-${i.content_version}`} item={i} />)}</div>
      )}
    </>
  );
}
