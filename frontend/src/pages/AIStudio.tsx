import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Badge, Button, Card, ErrorState, Field, PageHeader, SimBadge, Spinner } from "../components/ui";
import { useAction } from "../hooks/useApi";
import { useSettings } from "../hooks/useSettings";
import { get, patch, post, put } from "../lib/api";
import type { Clip } from "../types";

export function AIStudio() {
  const s = useSettings();
  const prompt = useQuery({ queryKey: ["prompt"], queryFn: () => get<{ version: string; system: string }>("/api/v1/ai/prompt-preview") });
  const costs = useQuery({ queryKey: ["costs"], queryFn: () => get<any>("/api/v1/ai/costs") });
  const clips = useQuery({ queryKey: ["clips", "ai"], queryFn: () => get<{ items: Clip[] }>("/api/v1/clips?page_size=50&status=AUTO_READY,NEEDS_REVIEW,APPROVED,ANALYZED") });
  const [ai, setAi] = useState<any>(null);
  const [key, setKey] = useState("");
  const [banned, setBanned] = useState("");
  const [clipId, setClipId] = useState("");
  const [alts, setAlts] = useState<any>(null);

  useEffect(() => { if (s.data && !ai) { setAi(s.data.settings.ai); setBanned(s.data.settings.ai.banned_words.join(", ")); } }, [s.data, ai]);
  const save = useAction(async () => {
    if (key) await put("/api/v1/settings/secrets", { name: "llm_api_key", value: key });
    return patch("/api/v1/settings", { ai: { ...ai, banned_words: banned.split(",").map((x) => x.trim()).filter(Boolean) } });
  }, { ok: "Impostazioni AI salvate (validate dal backend)", invalidate: [["settings"], ["prompt"]] });
  const gen = useAction(() => post("/api/v1/ai/alternatives", { clip_id: clipId, count: 3 }), { ok: "Alternative generate", invalidate: [["costs"]] });
  const apply = useAction((a: any) => patch(`/api/v1/reviews/${clipId}/content`, { overlay_title: a.overlay_title, caption: a.caption, hashtags: a.hashtags }), { ok: "Titolo applicato: render in corso" });

  if (s.isLoading || !ai) return <Spinner />;
  if (s.error) return <ErrorState error={s.error} />;
  const set = (k: string, v: any) => setAi({ ...ai, [k]: v });
  return (
    <>
      <PageHeader title="AI Studio" subtitle="Trascrizione locale (faster-whisper) + ragionamento via API configurabile." actions={<Button busy={save.isPending} onClick={() => save.mutate()}>Salva</Button>} />
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Provider e modello">
          <div className="space-y-3">
            <Field label="Provider" hint="«mock» = analisi SIMULATA: le clip reali analizzate così vanno sempre in revisione.">
              <select className="input" value={ai.provider} onChange={(e) => set("provider", e.target.value)}>
                <option value="mock">mock (simulato)</option><option value="anthropic">Anthropic</option><option value="openai">OpenAI-compatibile</option>
              </select>
            </Field>
            <Field label="Modello"><input className="input" value={ai.model} onChange={(e) => set("model", e.target.value)} placeholder="es. claude-sonnet-5-5" /></Field>
            <Field label="Base URL (opzionale)"><input className="input" value={ai.base_url} onChange={(e) => set("base_url", e.target.value)} /></Field>
            <Field label={`API key ${s.data?.secrets.llm_api_key ? "(già salvata — lascia vuoto per mantenerla)" : ""}`}><input className="input" type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)} placeholder={s.data?.secrets.llm_api_key ? "••••••••" : "non configurata"} /></Field>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={ai.vision_enabled} onChange={(e) => set("vision_enabled", e.target.checked)} />Analisi frame (vision) — costa di più</label>
            <div className="grid grid-cols-3 gap-2">
              <Field label="Max frame"><input className="input" type="number" min={1} max={16} value={ai.max_frames} onChange={(e) => set("max_frames", +e.target.value)} /></Field>
              <Field label="Ogni (s)"><input className="input" type="number" min={1} max={15} value={ai.frame_interval_seconds} onChange={(e) => set("frame_interval_seconds", +e.target.value)} /></Field>
              <Field label="Budget $/clip"><input className="input" type="number" step="0.01" value={ai.budget_usd_per_clip} onChange={(e) => set("budget_usd_per_clip", +e.target.value)} /></Field>
            </div>
          </div>
        </Card>
        <Card title="Stile e vincoli">
          <div className="space-y-3">
            <Field label="Preset di tono">
              <select className="input" value={ai.tone} onChange={(e) => set("tone", e.target.value)}>
                <option value="ironic_meme">Ironico / meme</option><option value="clickbait_safe">Clickbait non ingannevole</option><option value="competitive">Competitivo</option><option value="descriptive">Descrittivo</option>
              </select>
            </Field>
            <Field label="Istruzioni aggiuntive al prompt"><textarea className="input h-20" value={ai.prompt_extra} onChange={(e) => set("prompt_extra", e.target.value)} /></Field>
            <Field label="Parole vietate (separate da virgola)"><input className="input" value={banned} onChange={(e) => setBanned(e.target.value)} /></Field>
            <div className="grid grid-cols-3 gap-2">
              <Field label="Lingua"><input className="input" value={ai.language} onChange={(e) => set("language", e.target.value)} /></Field>
              <Field label="Max emoji"><input className="input" type="number" min={0} max={5} value={ai.max_emoji} onChange={(e) => set("max_emoji", +e.target.value)} /></Field>
              <Field label="Max char overlay"><input className="input" type="number" min={10} max={120} value={ai.max_overlay_chars} onChange={(e) => set("max_overlay_chars", +e.target.value)} /></Field>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <Field label="Modello Whisper"><input className="input" value={ai.whisper_model} onChange={(e) => set("whisper_model", e.target.value)} /></Field>
              <Field label="Dispositivo ASR"><select className="input" value={ai.whisper_device} onChange={(e) => set("whisper_device", e.target.value)}><option>auto</option><option>cpu</option><option>cuda</option></select></Field>
            </div>
          </div>
        </Card>
        <Card title="Prova su una clip" className="lg:col-span-2">
          <div className="flex flex-wrap items-end gap-2">
            <div className="min-w-[260px] flex-1"><Field label="Clip"><select className="input" value={clipId} onChange={(e) => { setClipId(e.target.value); setAlts(null); }}><option value="">Seleziona…</option>{clips.data?.items.map((c) => <option key={c.id} value={c.id}>{c.title} — {c.overlay_title ?? "senza overlay"}</option>)}</select></Field></div>
            <Button disabled={!clipId} busy={gen.isPending} onClick={() => gen.mutate(undefined, { onSuccess: setAlts })}>Genera alternative</Button>
          </div>
          <p className="mt-1 text-xs text-zinc-500">Riusa la trascrizione già salvata: nessun nuovo download né ASR.</p>
          {alts && (
            <div className="mt-3 space-y-2">
              {alts.items.map((a: any, i: number) => (
                <div key={i} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-zinc-800 p-3 text-sm">
                  <div><b>{a.overlay_title}</b>{a.simulated && <> <SimBadge /></>}<div className="text-xs text-zinc-400">{a.caption} {a.hashtags.join(" ")}</div></div>
                  <Button variant="ghost" busy={apply.isPending} onClick={() => apply.mutate(a)}>Usa questo</Button>
                </div>
              ))}
              <p className="text-xs text-zinc-500">Costo stimato: ${alts.estimated_cost_usd}</p>
            </div>
          )}
        </Card>
        <Card title="Prompt di sistema" actions={<Badge>{prompt.data?.version}</Badge>} className="lg:col-span-2">
          <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-lg bg-zinc-950 p-3 text-xs text-zinc-300">{prompt.data?.system}</pre>
          <p className="mt-2 text-xs text-zinc-500">Trascrizione, titolo e descrizioni dei frame sono inviati come dati non fidati, mai come istruzioni. Costi cumulati (stima): {costs.data?.analyses ?? 0} analisi · ${costs.data?.cost_usd_estimate ?? 0}</p>
        </Card>
      </div>
    </>
  );
}
