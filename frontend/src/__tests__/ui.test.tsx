import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { StatusBadge, ToastProvider } from "../components/ui";
import { ReviewInbox } from "../pages/ReviewInbox";
import { QueuePage } from "../pages/Queue";

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}><ToastProvider><MemoryRouter>{ui}</MemoryRouter></ToastProvider></QueryClientProvider>,
  );
}

function mockFetch(routes: Record<string, any>) {
  const calls: { url: string; method: string; body?: string }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method ?? "GET", body: init?.body as string });
    const key = Object.keys(routes).find((k) => url.startsWith(k));
    if (!key) return new Response(JSON.stringify({ error: { code: "nf", message: "non trovato" } }), { status: 404 });
    return new Response(JSON.stringify(routes[key]), { status: 200 });
  }));
  return calls;
}

afterEach(() => vi.unstubAllGlobals());

const item = (over = {}) => ({
  id: "c1", title: "Clip", overlay_title: "Titolo", caption: "Cap", hashtags: ["#gaming"], game_name: "GTA", creator_name: "x", has_original: true, has_render: true,
  is_demo: true, content_version: 1, transcript: "ciao", visual_notes: [], review: { state: "NEEDS_REVIEW", reasons: [{ code: "pii_detected", message: "Dati personali" }], flags: [], policy_version: "gate-1.0" }, ...over,
});

describe("UI", () => {
  it("StatusBadge mostra etichette italiane", () => {
    render(<StatusBadge status="NEEDS_REVIEW" />);
    expect(screen.getByText("Da rivedere")).toBeInTheDocument();
  });

  it("Review Inbox: stato vuoto", async () => {
    mockFetch({ "/api/v1/reviews": { items: [] } });
    wrap(<ReviewInbox />);
    expect(await screen.findByText("Nessuna clip da rivedere")).toBeInTheDocument();
  });

  it("Review Inbox: Approva chiama davvero il backend", async () => {
    const calls = mockFetch({ "/api/v1/reviews/c1/approve": item(), "/api/v1/reviews": { items: [item()] } });
    wrap(<ReviewInbox />);
    expect(await screen.findByText(/Dati personali/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Approva/ }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url === "/api/v1/reviews/c1/approve")).toBe(true));
  });

  it("Review Inbox: errore API visibile", async () => {
    mockFetch({});
    wrap(<ReviewInbox />);
    expect(await screen.findByRole("alert")).toHaveTextContent("non trovato");
  });

  it("Queue: raggruppa per giorno e mostra il limite", async () => {
    mockFetch({ "/api/v1/queue": { items: [{ id: "j1", clip_id: "c1", provider: "buffer", caption: "x", hashtags: [], due_at: "2026-10-23T08:00:00Z", status: "QUEUED_LOCAL", paused: false, simulated: false, remote_post_id: null, remote_status: null, remote_url: null, attempts: 0, next_attempt_at: null, error: null, last_reconciled_at: null, clip_title: "T", overlay_title: "Overlay" }], daily_counts: { "2026-10-23": 1 }, daily_max: 4, slots: ["10:00"], timezone: "Europe/Rome", publish_mode: "dry_run" } });
    wrap(<QueuePage />);
    expect(await screen.findByText("1/4 post")).toBeInTheDocument();
    expect(screen.getByText("Overlay")).toBeInTheDocument();
    expect(screen.getByText(/DRY RUN attivo/)).toBeInTheDocument();
  });
});
