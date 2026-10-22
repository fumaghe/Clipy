import { Activity, CalendarClock, Film, History, Inbox, LayoutDashboard, Palette, Settings as Cog, Sparkles } from "lucide-react";
import { NavLink, Route, Routes } from "react-router-dom";
import { Badge, ErrorState, Spinner } from "../components/ui";
import { useStatus } from "../hooks/useApi";
import { AIStudio } from "../pages/AIStudio";
import { Library } from "../pages/Library";
import { Overview } from "../pages/Overview";
import { PublishingHistory } from "../pages/PublishingHistory";
import { QueuePage } from "../pages/Queue";
import { ReviewInbox } from "../pages/ReviewInbox";
import { SettingsPage } from "../pages/Settings";
import { TemplateStudio } from "../pages/TemplateStudio";

const NAV = [
  { to: "/", label: "Overview", icon: LayoutDashboard },
  { to: "/library", label: "Clip Library", icon: Film },
  { to: "/review", label: "Review Inbox", icon: Inbox, badge: "needs_review" },
  { to: "/ai", label: "AI Studio", icon: Sparkles },
  { to: "/templates", label: "Template Studio", icon: Palette },
  { to: "/queue", label: "Coda / Calendario", icon: CalendarClock },
  { to: "/history", label: "Storico pubblicazioni", icon: History },
  { to: "/settings", label: "Settings", icon: Cog },
];

function Header() {
  const { data: s } = useStatus();
  if (!s) return <div className="h-12 border-b border-zinc-800" />;
  const mode = s.publish_mode === "live" ? <Badge tone="red">LIVE</Badge> : s.publish_mode === "mock" ? <Badge tone="blue">DEMO PUBLISHER</Badge> : <Badge tone="amber">DRY RUN</Badge>;
  const busy = (s.counts.processing ?? 0) + (s.counts.new ?? 0);
  return (
    <header className="flex flex-wrap items-center gap-2 border-b border-zinc-800 px-4 py-2.5 text-xs">
      {mode}
      {s.demo_mode && <Badge tone="blue" title="Twitch simulato: nessuna credenziale configurata">DEMO</Badge>}
      <Badge tone={s.twitch.authorized ? "green" : s.twitch.simulated ? "blue" : "amber"}>Twitch {s.twitch.authorized ? "collegato" : s.twitch.simulated ? "simulato" : "non collegato"}</Badge>
      <Badge tone={s.buffer.connected ? "green" : "amber"}>Buffer {s.buffer.connected ? "collegato" : "non collegato"}</Badge>
      <Badge tone={s.ffmpeg ? "green" : "red"}>FFmpeg {s.ffmpeg ? "OK" : "mancante"}</Badge>
      <Badge tone={s.worker.leader ? "green" : "zinc"}>Worker {s.worker.leader ? "attivo" : "fermo"}</Badge>
      <Badge tone={s.collector_enabled ? "green" : "zinc"}>Collector {s.collector_enabled ? "ON" : "OFF"}</Badge>
      {busy > 0 && <span className="ml-auto inline-flex items-center gap-1 text-violet-300"><Activity className="h-3.5 w-3.5 animate-pulse" />{busy} clip in elaborazione</span>}
    </header>
  );
}

export function App() {
  const { data: s, isLoading, error } = useStatus();
  return (
    <div className="flex min-h-screen">
      <aside className="sticky top-0 hidden h-screen w-56 shrink-0 flex-col border-r border-zinc-800 bg-zinc-950 p-3 md:flex">
        <div className="mb-6 px-2 pt-2 text-lg font-black tracking-[0.25em] text-white">CLIPY</div>
        <nav className="flex flex-col gap-1">
          {NAV.map(({ to, label, icon: Icon, badge }) => (
            <NavLink key={to} to={to} end={to === "/"} className={({ isActive }) => `flex items-center gap-2 rounded-lg px-3 py-2 text-sm ${isActive ? "bg-brand-600/20 text-white" : "text-zinc-400 hover:bg-zinc-900 hover:text-zinc-100"}`}>
              <Icon className="h-4 w-4" />
              <span className="flex-1">{label}</span>
              {badge && (s?.counts[badge] ?? 0) > 0 && <span className="rounded-full bg-amber-500 px-1.5 text-xs font-bold text-black">{s?.counts[badge]}</span>}
            </NavLink>
          ))}
        </nav>
        <p className="mt-auto px-2 text-[11px] text-zinc-600">Clipy 1.0 · single-user · locale</p>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <nav className="flex gap-1 overflow-x-auto border-b border-zinc-800 p-2 md:hidden">
          {NAV.map(({ to, label }) => (
            <NavLink key={to} to={to} end={to === "/"} className={({ isActive }) => `whitespace-nowrap rounded-md px-2 py-1 text-xs ${isActive ? "bg-brand-600/30 text-white" : "text-zinc-400"}`}>{label}</NavLink>
          ))}
        </nav>
        <Header />
        <main className="mx-auto w-full max-w-7xl flex-1 p-4 md:p-6">
          {isLoading ? <Spinner label="Connessione al backend…" /> : error ? <ErrorState error={error} /> : (
            <Routes>
              <Route path="/" element={<Overview />} />
              <Route path="/library" element={<Library />} />
              <Route path="/review" element={<ReviewInbox />} />
              <Route path="/ai" element={<AIStudio />} />
              <Route path="/templates" element={<TemplateStudio />} />
              <Route path="/queue" element={<QueuePage />} />
              <Route path="/history" element={<PublishingHistory />} />
              <Route path="/settings" element={<SettingsPage />} />
              <Route path="*" element={<ErrorState error={new Error("Pagina non trovata")} />} />
            </Routes>
          )}
        </main>
      </div>
    </div>
  );
}
