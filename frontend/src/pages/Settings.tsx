import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Badge, Button, Card, ErrorState, Field, PageHeader, Spinner } from "../components/ui";
import { useAction, useStatus } from "../hooks/useApi";
import { useSettings } from "../hooks/useSettings";
import { patch, post, put } from "../lib/api";
import { fmtDateTime } from "../lib/format";

function SecretInput({ name, label, configured, hint }: { name: string; label: string; configured: boolean; hint?: string }) {
  const [v, setV] = useState("");
  const save = useAction(() => put("/api/v1/settings/secrets", { name, value: v }), { ok: `${label} salvato`, invalidate: [["settings"], ["status"]] });
  return (
    <Field label={label} hint={hint ?? "Salvato solo nel database locale; non viene mai mostrato né loggato."}>
      <div className="flex gap-2">
        <input className="input" type="password" autoComplete="off" value={v} onChange={(e) => setV(e.target.value)} placeholder={configured ? "•••••••• (configurato)" : "non configurato"} />
        <Button variant="ghost" disabled={!v} busy={save.isPending} onClick={() => save.mutate(undefined, { onSuccess: () => setV("") })}>Salva</Button>
        {configured && <Button variant="ghost" onClick={() => put("/api/v1/settings/secrets", { name, value: "" }).then(() => save.reset())}>Rimuovi</Button>}
      </div>
    </Field>
  );
}

function TwitchCard({ s, secrets }: { s: any; secrets: Record<string, boolean> }) {
  const { data: st } = useStatus();
  const [login, setLogin] = useState(s.twitch.login);
  const [poll, setPoll] = useState(s.twitch.polling_minutes);
  const [boot, setBoot] = useState(s.twitch.bootstrap_hours);
  const [params] = useSearchParams();
  const saveCfg = useAction(() => patch("/api/v1/settings", { twitch: { login, polling_minutes: poll, bootstrap_hours: boot } }), { ok: "Impostazioni Twitch salvate", invalidate: [["settings"], ["status"]] });
  const connect = useAction(() => post("/api/v1/integrations/twitch/connect"), {});
  const test = useAction(() => post("/api/v1/integrations/twitch/test"), { ok: (r: any) => `Canale ${r.login} → broadcaster_id ${r.broadcaster_id}${r.simulated ? " (SIMULATO)" : ""}` });
  const sync = useAction(() => post("/api/v1/clips/sync"), { ok: (r: any) => `Import: ${r.found} clip, ${r.new} nuove${r.simulated ? " (simulato)" : ""}` });
  const toggleDemo = useAction((demo: boolean) => patch("/api/v1/settings", { system: { demo_mode: demo } }), { ok: "Modalità aggiornata" });
  return (
    <Card title="Twitch" actions={<Badge tone={st?.twitch.authorized ? "green" : "amber"}>{st?.twitch.authorized ? "Autorizzato" : st?.twitch.simulated ? "Simulato" : "Non autorizzato"}</Badge>}>
      {params.get("twitch") === "ok" && <p className="mb-2 text-sm text-emerald-300">Account Twitch collegato.</p>}
      {params.get("twitch") === "error" && <p className="mb-2 text-sm text-red-300">Collegamento Twitch non riuscito.</p>}
      <div className="space-y-3">
        <div className="grid gap-3 md:grid-cols-3">
          <Field label="Canale (login)"><input className="input" value={login} onChange={(e) => setLogin(e.target.value)} /></Field>
          <Field label="Polling (minuti)"><input className="input" type="number" min={1} value={poll} onChange={(e) => setPoll(+e.target.value)} /></Field>
          <Field label="Backfill primo avvio (ore)"><input className="input" type="number" min={1} value={boot} onChange={(e) => setBoot(+e.target.value)} /></Field>
        </div>
        <SecretInput name="twitch_client_id" label="Client ID" configured={secrets.twitch_client_id} />
        <SecretInput name="twitch_client_secret" label="Client Secret" configured={secrets.twitch_client_secret} />
        <p className="text-xs text-zinc-500">Redirect URI da registrare nell'app Twitch: <code>{s.__redirect}</code>. Scope richiesto: <code>channel:manage:clips</code> (NEEDS_LIVE_VERIFICATION).</p>
        <div className="flex flex-wrap gap-2">
          <Button busy={saveCfg.isPending} onClick={() => saveCfg.mutate()}>Salva</Button>
          <Button variant="ghost" busy={connect.isPending} onClick={() => connect.mutate(undefined, { onSuccess: (r: any) => { window.location.href = r.authorize_url; } })}>Collega account Twitch</Button>
          <Button variant="ghost" busy={test.isPending} onClick={() => test.mutate()}>Testa connessione</Button>
          <Button variant="ghost" busy={sync.isPending} onClick={() => sync.mutate()}>Importa ora (finestra {boot}h al primo avvio)</Button>
          <Button variant="ghost" onClick={() => toggleDemo.mutate(!s.system.demo_mode)}>{s.system.demo_mode ? "Esci dalla modalità demo" : "Torna alla modalità demo"}</Button>
        </div>
        {st?.twitch.last_scan_at && <p className="text-xs text-zinc-500">Ultima scansione: {fmtDateTime(st.twitch.last_scan_at)} {st.twitch.last_error && <span className="text-red-300">· {st.twitch.last_error}</span>}</p>}
      </div>
    </Card>
  );
}

function BufferWizard({ s, secrets }: { s: any; secrets: Record<string, boolean> }) {
  const [channels, setChannels] = useState<any[] | null>(null);
  const [sim, setSim] = useState(false);
  const test = useAction(() => post("/api/v1/integrations/buffer/test"), { ok: "Test riuscito (nessun post creato)", invalidate: [] });
  const select = useAction((id: string) => post("/api/v1/integrations/buffer/select", { channel_id: id }), { ok: "Canale TikTok collegato", invalidate: [["settings"], ["status"]] });
  const provider = useAction((p: string) => patch("/api/v1/settings", { publishing: { provider: p } }), { ok: "Publisher aggiornato" });
  const steps = [
    { done: true, t: "1. Crea un account Buffer e collega il tuo TikTok", d: "Fuori da Clipy, su buffer.com. Clipy non chiede mai la password di TikTok." },
    { done: secrets.buffer_api_key, t: "2. Genera una API key personale", d: "Dalle impostazioni sviluppatore di Buffer, poi incollala qui sotto." },
    { done: !!channels || s.publishing.is_connected, t: "3. Testa i permessi (non pubblica nulla)", d: "Elenca organizzazioni e canali." },
    { done: s.publishing.is_connected, t: "4. Seleziona il canale TikTok", d: "Verifica che sia TikTok e non disconnesso." },
  ];
  return (
    <Card title="Buffer · wizard di primo setup" actions={<Badge tone={s.publishing.is_connected ? "green" : "amber"}>{s.publishing.is_connected ? "Collegato" : "Non collegato"}</Badge>}>
      <ol className="mb-3 space-y-1 text-sm">{steps.map((x) => <li key={x.t} className={x.done ? "text-emerald-300" : "text-zinc-300"}>{x.done ? "✓ " : "○ "}<b>{x.t}</b> <span className="text-zinc-500">— {x.d}</span></li>)}</ol>
      <div className="space-y-3">
        <SecretInput name="buffer_api_key" label="Buffer API key" configured={secrets.buffer_api_key} />
        <div className="flex flex-wrap gap-2">
          <Button variant="ghost" busy={test.isPending} onClick={() => test.mutate(undefined, { onSuccess: (r: any) => { setChannels(r.channels); setSim(r.simulated); } })}>Testa e elenca canali</Button>
          <Button variant="ghost" onClick={() => provider.mutate(s.publishing.provider === "mock" ? "buffer" : "mock")}>{s.publishing.provider === "mock" ? "Usa Buffer reale" : "Usa publisher DEMO (simulato)"}</Button>
        </div>
        {sim && <Badge tone="blue">Canali SIMULATI (publisher demo)</Badge>}
        {channels && (
          <ul className="space-y-1 text-sm">
            {channels.length === 0 && <li className="text-zinc-500">Nessun canale trovato.</li>}
            {channels.map((c) => (
              <li key={c.id} className="flex items-center justify-between rounded-lg border border-zinc-800 p-2">
                <span>{c.name} <span className="text-xs text-zinc-500">({c.service}{c.disconnected ? ", disconnesso" : ""}) · <code>{c.id}</code></span></span>
                <Button variant="ghost" busy={select.isPending} onClick={() => select.mutate(c.id)}>{s.publishing.channel_id === c.id ? "Selezionato ✓" : "Usa questo"}</Button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Card>
  );
}

function PublishingCard({ s }: { s: any }) {
  const [p, setP] = useState({ daily_max: s.publishing.daily_max, slots: s.publishing.slots.join(", "), timezone: s.publishing.timezone, remote: s.publishing.remote_max_scheduled, cap: s.publishing.max_caption_length });
  const save = useAction(() => patch("/api/v1/settings", { publishing: { daily_max: p.daily_max, slots: p.slots.split(",").map((x: string) => x.trim()).filter(Boolean), timezone: p.timezone, remote_max_scheduled: p.remote, max_caption_length: p.cap } }), { ok: "Pianificazione salvata", invalidate: [["settings"], ["queue"], ["status"]] });
  return (
    <Card title="Pianificazione">
      <div className="grid gap-3 md:grid-cols-3">
        <Field label="Post al giorno (max)"><input className="input" type="number" min={1} max={25} value={p.daily_max} onChange={(e) => setP({ ...p, daily_max: +e.target.value })} /></Field>
        <Field label="Slot (HH:MM, separati da virgola)"><input className="input" value={p.slots} onChange={(e) => setP({ ...p, slots: e.target.value })} /></Field>
        <Field label="Fuso orario"><input className="input" value={p.timezone} onChange={(e) => setP({ ...p, timezone: e.target.value })} /></Field>
        <Field label="Max post remoti pianificati" hint="Buffer Free: 10 per canale. Il resto resta nella coda locale."><input className="input" type="number" min={1} value={p.remote} onChange={(e) => setP({ ...p, remote: +e.target.value })} /></Field>
        <Field label="Lunghezza max caption"><input className="input" type="number" min={50} max={5000} value={p.cap} onChange={(e) => setP({ ...p, cap: +e.target.value })} /></Field>
      </div>
      <div className="mt-3"><Button busy={save.isPending} onClick={() => save.mutate()}>Salva</Button></div>
    </Card>
  );
}

function StorageCard({ s, secrets }: { s: any; secrets: Record<string, boolean> }) {
  const [st, setSt] = useState(s.storage);
  const save = useAction(() => patch("/api/v1/settings", { storage: st }), { ok: "Storage salvato", invalidate: [["settings"], ["status"]] });
  const test = useAction(() => post("/api/v1/integrations/storage/test"), { ok: (r: any) => r.message, invalidate: [] });
  return (
    <Card title="Storage media (R2 / S3)">
      <div className="grid gap-3 md:grid-cols-2">
        <Field label="Provider"><select className="input" value={st.provider} onChange={(e) => setSt({ ...st, provider: e.target.value })}><option value="local">Locale (mock, non pubblico)</option><option value="s3">S3 / Cloudflare R2</option></select></Field>
        <Field label="Bucket"><input className="input" value={st.bucket} onChange={(e) => setSt({ ...st, bucket: e.target.value })} /></Field>
        <Field label="Endpoint"><input className="input" value={st.endpoint_url} onChange={(e) => setSt({ ...st, endpoint_url: e.target.value })} placeholder="https://<account>.r2.cloudflarestorage.com" /></Field>
        <Field label="URL pubblico HTTPS stabile" hint="Niente presigned URL: deve restare valido fino alla pubblicazione."><input className="input" value={st.public_base_url} onChange={(e) => setSt({ ...st, public_base_url: e.target.value })} /></Field>
      </div>
      <div className="mt-3 grid gap-3 md:grid-cols-2">
        <SecretInput name="s3_access_key_id" label="Access key ID" configured={secrets.s3_access_key_id} />
        <SecretInput name="s3_secret_access_key" label="Secret access key" configured={secrets.s3_secret_access_key} />
      </div>
      <p className="mt-2 text-xs text-amber-300">Attenzione privacy: i video caricati sono accessibili senza autenticazione (nome opaco) fino alla pubblicazione.</p>
      <div className="mt-3 flex gap-2"><Button busy={save.isPending} onClick={() => save.mutate()}>Salva</Button><Button variant="ghost" busy={test.isPending} onClick={() => test.mutate()}>Testa upload + accesso pubblico</Button></div>
    </Card>
  );
}

function LiveCard({ s, phrase }: { s: any; phrase: string }) {
  const [txt, setTxt] = useState("");
  const [ok, setOk] = useState(false);
  const enable = useAction(() => post("/api/v1/settings/live/enable", { confirm: txt }), { ok: "MODALITÀ LIVE abilitata", invalidate: [["settings"], ["status"]] });
  const disable = useAction(() => post("/api/v1/settings/live/disable"), { ok: "Tornato in DRY RUN", invalidate: [["settings"], ["status"]] });
  const live = s.publishing.live_enabled;
  return (
    <Card title="Pubblicazione reale" actions={<Badge tone={live ? "red" : "amber"}>{live ? "LIVE" : "DRY RUN"}</Badge>}>
      {live ? (
        <div className="space-y-2"><p className="text-sm">Le clip idonee vengono pianificate su Buffer/TikTok in automatico; solo le dubbie richiedono revisione.</p><Button variant="danger" onClick={() => disable.mutate()}>Disattiva (torna in DRY RUN)</Button></div>
      ) : (
        <div className="space-y-3">
          <p className="text-sm text-zinc-300">Default: <b>DRY RUN</b> — nessuna richiesta di creazione post viene inviata. Per attivare il live servono Buffer collegato, canale TikTok selezionato e storage S3/R2 pubblico. L'attivazione è una decisione deliberata del proprietario dell'account.</p>
          <label className="flex items-start gap-2 text-sm"><input type="checkbox" className="mt-1" checked={ok} onChange={(e) => setOk(e.target.checked)} />Confermo di essere il proprietario dell'account TikTok e autorizzo Clipy a pubblicare in automatico le clip idonee.</label>
          <Field label={`Digita «${phrase}»`}><input className="input" value={txt} onChange={(e) => setTxt(e.target.value)} /></Field>
          <Button variant="danger" disabled={!ok || txt !== phrase} busy={enable.isPending} onClick={() => enable.mutate()}>Abilita pubblicazione reale</Button>
        </div>
      )}
    </Card>
  );
}

function ReviewCard({ s }: { s: any }) {
  const [r, setR] = useState(s.review);
  const [ret, setRet] = useState(s.system.retention_days);
  const save = useAction(() => patch("/api/v1/settings", { review: r, system: { retention_days: ret } }), { ok: "Review gate salvata", invalidate: [["settings"]] });
  return (
    <Card title="Review gate e retention">
      <div className="grid gap-3 md:grid-cols-3">
        <Field label="Preset"><select className="input" value={r.preset} onChange={(e) => setR({ ...r, preset: e.target.value })}><option value="prudent">Prudente</option><option value="balanced">Bilanciato</option><option value="permissive">Permissivo</option></select></Field>
        <Field label={`Soglia confidenza: ${r.min_confidence}`} hint="Segnale non calibrato: non è l'unico criterio."><input type="range" className="w-full accent-violet-500" min={0} max={1} step={0.05} value={r.min_confidence} onChange={(e) => setR({ ...r, min_confidence: +e.target.value })} /></Field>
        <Field label="Retention file (giorni)"><input className="input" type="number" min={1} value={ret} onChange={(e) => setRet(+e.target.value)} /></Field>
      </div>
      <div className="mt-2 flex flex-wrap gap-4 text-sm">
        <label className="flex items-center gap-2"><input type="checkbox" checked={r.require_evidence} onChange={(e) => setR({ ...r, require_evidence: e.target.checked })} />Richiedi evidenze</label>
        <label className="flex items-center gap-2"><input type="checkbox" checked={r.detect_pii} onChange={(e) => setR({ ...r, detect_pii: e.target.checked })} />Rileva dati personali</label>
        <label className="flex items-center gap-2"><input type="checkbox" checked={r.review_landscape_crop} onChange={(e) => setR({ ...r, review_landscape_crop: e.target.checked })} />Rivedi i crop landscape</label>
      </div>
      <div className="mt-3"><Button busy={save.isPending} onClick={() => save.mutate()}>Salva</Button></div>
    </Card>
  );
}

function HealthCard() {
  const { data: s } = useStatus();
  if (!s) return null;
  return (
    <Card title="Health">
      <ul className="space-y-1 text-sm">
        <li>FFmpeg: {s.ffmpeg ? <Badge tone="green">{s.ffmpeg.slice(0, 40)}</Badge> : <Badge tone="red">mancante</Badge>}</li>
        <li>ffprobe: {s.ffprobe ? <Badge tone="green">OK</Badge> : <Badge tone="red">mancante</Badge>}</li>
        <li>faster-whisper: {s.asr.faster_whisper_installed ? <Badge tone="green">installato</Badge> : <Badge tone="amber">non installato (pip install faster-whisper)</Badge>} · GPU CUDA: {s.asr.cuda_available ? "sì" : "no (CPU)"}</li>
        <li>Worker: {s.worker.leader ? <Badge tone="green">leader attivo</Badge> : <Badge>non attivo</Badge>}</li>
        <li>Modalità pubblicazione: <Badge tone={s.publish_mode === "live" ? "red" : "amber"}>{s.publish_mode}</Badge></li>
      </ul>
    </Card>
  );
}

export function SettingsPage() {
  const q = useSettings();
  if (q.isLoading) return <Spinner />;
  if (q.error) return <ErrorState error={q.error} retry={() => q.refetch()} />;
  const { settings, secrets, live_confirm_phrase, redirect_uri } = q.data!;
  const s = { ...settings, __redirect: redirect_uri };
  return (
    <>
      <PageHeader title="Settings" subtitle="Le chiavi non vengono mai mostrate dopo il salvataggio." />
      <div className="space-y-4">
        <HealthCard />
        <TwitchCard s={s} secrets={secrets} />
        <PublishingCard s={s} />
        <BufferWizard s={s} secrets={secrets} />
        <StorageCard s={s} secrets={secrets} />
        <ReviewCard s={s} />
        <LiveCard s={s} phrase={live_confirm_phrase} />
      </div>
    </>
  );
}
