import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Badge, Button, Card, ErrorState, Field, PageHeader, Spinner } from "../components/ui";
import { useAction } from "../hooks/useApi";
import { get, post, postBlob, put } from "../lib/api";
import type { Clip, Template, TemplateConfig } from "../types";

function Num({ label, value, onChange, min, max, step = 1 }: { label: string; value: number; onChange: (n: number) => void; min: number; max: number; step?: number }) {
  return (
    <Field label={`${label}: ${value}`}>
      <input type="range" className="w-full accent-violet-500" value={value} min={min} max={max} step={step} onChange={(e) => onChange(+e.target.value)} />
    </Field>
  );
}

export function TemplateStudio() {
  const list = useQuery({ queryKey: ["templates"], queryFn: () => get<{ items: Template[]; active_template_id: string; font_resolved: string | null; font_warnings: string[] }>("/api/v1/templates") });
  const clips = useQuery({ queryKey: ["clips", "tpl"], queryFn: () => get<{ items: Clip[] }>("/api/v1/clips?page_size=50&status=AUTO_READY,NEEDS_REVIEW,APPROVED,ANALYZED") });
  const [tplId, setTplId] = useState<string>("");
  const [cfg, setCfg] = useState<TemplateConfig | null>(null);
  const [name, setName] = useState("");
  const [text, setText] = useState("Titolo di esempio che va a capo se è lungo");
  const [clipId, setClipId] = useState("");
  const [img, setImg] = useState<string | null>(null);
  const [previewErr, setPreviewErr] = useState<string | null>(null);
  const [at, setAt] = useState<number | "">("");
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (list.data && !tplId) {
      const t = list.data.items.find((x) => x.active) ?? list.data.items[0];
      if (t) { setTplId(t.id); setCfg(t.config); setName(t.name); }
    }
  }, [list.data, tplId]);
  const current = list.data?.items.find((t) => t.id === tplId);

  const preview = useAction(async () => {
    const blob = await postBlob(`/api/v1/templates/${tplId || "draft"}/preview`, { config: cfg, text, clip_id: clipId || null, at_seconds: at === "" ? null : at });
    return URL.createObjectURL(blob);
  }, { invalidate: [] });
  const doPreview = () => preview.mutate(undefined, { onSuccess: (u) => { setImg((old) => { if (old) URL.revokeObjectURL(old); return u; }); setPreviewErr(null); }, onError: (e) => setPreviewErr((e as Error).message) });
  const save = useAction(() => put(`/api/v1/templates/${tplId}`, { name, config: cfg }), { ok: "Template salvato (nuova versione)", invalidate: [["templates"]] });
  const saveNew = useAction(() => post("/api/v1/templates", { name: `${name} (copia)`, config: cfg }), { ok: "Nuovo template creato", invalidate: [["templates"]] });
  const activate = useAction(() => post(`/api/v1/templates/${tplId}/activate`), { ok: "Template attivo", invalidate: [["templates"], ["settings"]] });
  const apply = useAction(() => post(`/api/v1/templates/${tplId}/apply`, {}), { ok: (r: any) => `${r.rerender} clip in ri-render con il template`, invalidate: [["clips"], ["status"]] });
  const verify = useAction((v: boolean) => put(`/api/v1/templates/${tplId}`, { name, config: cfg, reference_verified: v }), { ok: "Stato riferimento aggiornato", invalidate: [["templates"]] });

  if (list.isLoading || !cfg) return <Spinner />;
  if (list.error) return <ErrorState error={list.error} />;
  const up = <K extends keyof TemplateConfig>(k: K, v: TemplateConfig[K]) => setCfg({ ...cfg, [k]: v });
  const upd = (k: string, v: number | string) => setCfg({ ...cfg, duration: { ...cfg.duration, [k]: v } as TemplateConfig["duration"] });

  const uploadRef = async (f: File) => {
    const fd = new FormData();
    fd.append("file", f);
    const r = await fetch(`/api/v1/templates/${tplId}/reference`, { method: "POST", body: fd });
    if (!r.ok) setPreviewErr((await r.json()).error?.message ?? "Upload fallito"); else setPreviewErr(null);
  };

  return (
    <>
      <PageHeader title="Template Studio" subtitle="La preview usa lo stesso motore FFmpeg del render finale."
        actions={<><Button variant="ghost" busy={saveNew.isPending} onClick={() => saveNew.mutate()}>Salva come nuovo</Button><Button busy={save.isPending} onClick={() => save.mutate()}>Salva</Button><Button variant="ghost" busy={activate.isPending} disabled={current?.active} onClick={() => activate.mutate()}>{current?.active ? "Template attivo" : "Imposta come attivo"}</Button><Button variant="ghost" busy={apply.isPending} onClick={() => apply.mutate()}>Applica alle clip non pubblicate</Button></>} />
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <select className="input max-w-xs" value={tplId} onChange={(e) => { const t = list.data!.items.find((x) => x.id === e.target.value)!; setTplId(t.id); setCfg(t.config); setName(t.name); }}>
          {list.data?.items.map((t) => <option key={t.id} value={t.id}>{t.name} v{t.version}{t.active ? " ★" : ""}</option>)}
        </select>
        <input className="input max-w-xs" value={name} onChange={(e) => setName(e.target.value)} />
        <Badge tone={current?.reference_verified ? "green" : "amber"}>{current?.reference_verified ? "Riferimento verificato" : "REFERENCE_NOT_VERIFIED"}</Badge>
        {list.data?.font_warnings.length ? <Badge tone="amber">font: fallback in uso</Badge> : null}
      </div>
      <div className="grid gap-4 lg:grid-cols-[1fr_360px]">
        <div className="space-y-4">
          <Card title="Testo">
            <div className="grid gap-3 md:grid-cols-2">
              <Field label="Colore testo"><input type="color" className="h-9 w-full rounded border border-zinc-700 bg-zinc-900" value={cfg.text_color} onChange={(e) => up("text_color", e.target.value)} /></Field>
              <Field label="Colore contorno"><input type="color" className="h-9 w-full rounded border border-zinc-700 bg-zinc-900" value={cfg.stroke_color} onChange={(e) => up("stroke_color", e.target.value)} /></Field>
              <Num label="Dimensione font" value={cfg.font_size} min={24} max={200} onChange={(v) => up("font_size", v)} />
              <Num label="Spessore contorno" value={cfg.stroke_width} min={0} max={24} onChange={(v) => up("stroke_width", v)} />
              <Num label="Posizione verticale (%)" value={cfg.y_percent} min={5} max={95} onChange={(v) => up("y_percent", v)} />
              <Num label="Offset verticale (px)" value={cfg.vertical_offset} min={-600} max={600} onChange={(v) => up("vertical_offset", v)} />
              <Num label="Larghezza max (%)" value={cfg.max_width_percent} min={30} max={100} onChange={(v) => up("max_width_percent", v)} />
              <Num label="Righe max" value={cfg.max_lines} min={1} max={6} onChange={(v) => up("max_lines", v)} />
              <Field label="Allineamento"><select className="input" value={cfg.align} onChange={(e) => up("align", e.target.value as any)}><option value="center">Centro</option><option value="left">Sinistra</option><option value="right">Destra</option></select></Field>
              <Field label="Font (file)" hint={list.data?.font_resolved ?? "non trovato"}><input className="input" value={cfg.font_file} onChange={(e) => up("font_file", e.target.value)} /></Field>
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={cfg.shadow} onChange={(e) => up("shadow", e.target.checked)} />Ombra</label>
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={cfg.background_box} onChange={(e) => up("background_box", e.target.checked)} />Sfondo box</label>
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={cfg.uppercase} onChange={(e) => up("uppercase", e.target.checked)} />MAIUSCOLO</label>
            </div>
          </Card>
          <Card title="Durata scritta e animazione">
            <div className="grid gap-3 md:grid-cols-2">
              <Field label="Modalità"><select className="input" value={cfg.duration.mode} onChange={(e) => upd("mode", e.target.value)}><option value="full">Intera clip</option><option value="first_seconds">Primi N secondi</option><option value="time_range">Intervallo custom</option></select></Field>
              {cfg.duration.mode === "first_seconds" && <Num label="Secondi" value={cfg.duration.seconds} min={0.5} max={30} step={0.5} onChange={(v) => upd("seconds", v)} />}
              {cfg.duration.mode === "time_range" && (<><Num label="Inizio (s)" value={cfg.duration.start} min={0} max={30} step={0.5} onChange={(v) => upd("start", v)} /><Num label="Fine (s)" value={cfg.duration.end} min={0.5} max={60} step={0.5} onChange={(v) => upd("end", v)} /></>)}
              <Num label="Fade-in (s)" value={cfg.fade_in} min={0} max={2} step={0.05} onChange={(v) => up("fade_in", v)} />
              <Num label="Fade-out (s)" value={cfg.fade_out} min={0} max={2} step={0.05} onChange={(v) => up("fade_out", v)} />
            </div>
          </Card>
          <Card title="Conversione da landscape">
            <div className="grid gap-3 md:grid-cols-2">
              <Field label="Modalità"><select className="input" value={cfg.landscape_mode} onChange={(e) => up("landscape_mode", e.target.value as any)}><option value="crop">Crop centrato</option><option value="letterbox">Letterbox con sfondo sfocato</option></select></Field>
              <Num label="Offset crop X" value={cfg.crop_offset_x} min={-1} max={1} step={0.05} onChange={(v) => up("crop_offset_x", v)} />
            </div>
          </Card>
          <Card title="Video di riferimento TikTok">
            <p className="text-sm text-zinc-400">Il riferimento non è stato verificato automaticamente: importa uno screenshot o un video per replicare stile e timing, poi conferma tu stesso quando il template è fedele.</p>
            <div className="mt-3 flex flex-wrap gap-2">
              <input ref={fileRef} type="file" accept=".png,.jpg,.jpeg,.mp4,.mov,.webm" hidden onChange={(e) => e.target.files?.[0] && uploadRef(e.target.files[0])} />
              <Button variant="ghost" onClick={() => fileRef.current?.click()}>Importa screenshot/video</Button>
              <Button variant="ghost" busy={verify.isPending} onClick={() => verify.mutate(!current?.reference_verified)}>{current?.reference_verified ? "Segna come NON verificato" : "Segna come verificato"}</Button>
            </div>
          </Card>
        </div>
        <Card title="Anteprima reale" className="h-fit lg:sticky lg:top-4">
          <div className="space-y-3">
            <Field label="Testo di prova"><input className="input" value={text} onChange={(e) => setText(e.target.value)} /></Field>
            <Field label="Clip sorgente"><select className="input" value={clipId} onChange={(e) => setClipId(e.target.value)}><option value="">Fixture landscape di esempio</option>{clips.data?.items.filter((c) => c.has_original).map((c) => <option key={c.id} value={c.id}>{c.title}</option>)}</select></Field>
            <Field label="Istante (s, vuoto = centro finestra)"><input className="input" type="number" min={0} step={0.5} value={at} onChange={(e) => setAt(e.target.value === "" ? "" : +e.target.value)} /></Field>
            <Button busy={preview.isPending} onClick={doPreview} className="w-full">Genera anteprima</Button>
            {previewErr && <ErrorState error={new Error(previewErr)} />}
            {img ? <img src={img} alt="anteprima render" className="mx-auto w-full max-w-[300px] rounded-lg border border-zinc-700" /> : <div className="flex aspect-[9/16] items-center justify-center rounded-lg border border-dashed border-zinc-700 text-xs text-zinc-500">Premi «Genera anteprima»</div>}
          </div>
        </Card>
      </div>
    </>
  );
}
