import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RemoteStateBadge, ToastProvider } from "../components/ui";
import { DiagnosticsCard } from "../pages/Settings";

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><ToastProvider><MemoryRouter>{ui}</MemoryRouter></ToastProvider></QueryClientProvider>);
}

const entry = (over: Record<string, unknown>) => ({
  id: "x", label: "X", state: "NOT_CONFIGURED", simulated: false, summary: "", hints: [], test_requires_confirm: false, confirm_text: null, last_test: null, details: {}, ...over,
});

function mockApi(items: unknown[]) {
  const calls: { url: string; method: string; body?: string }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method ?? "GET", body: init?.body as string });
    if (url.endsWith("/diagnostics")) return new Response(JSON.stringify({ items }), { status: 200 });
    return new Response(JSON.stringify(entry({ id: "storage", label: "Storage R2/S3", state: "CONNECTED" })), { status: 200 });
  }));
  return calls;
}

afterEach(() => vi.unstubAllGlobals());

describe("Diagnostica integrazioni", () => {
  it("mostra stati distinti e distingue le simulazioni", async () => {
    mockApi([
      entry({ id: "twitch", label: "Twitch", state: "NOT_CONFIGURED", simulated: true, summary: "Modalità DEMO" }),
      entry({ id: "buffer", label: "Buffer / TikTok", state: "NEEDS_LIVE_VERIFICATION", last_test: { at: "2026-10-22T10:00:00Z", ok: true, message: "Lettura riuscita", duration_ms: 5 } }),
      entry({ id: "llm", label: "Provider LLM", state: "TEST_FAILED", last_test: { at: "2026-10-22T10:00:00Z", ok: false, message: "Chiave rifiutata", duration_ms: 5 } }),
      entry({ id: "ffmpeg", label: "FFmpeg", state: "CONNECTED" }),
      entry({ id: "whisper", label: "Whisper", state: "CONFIGURED" }),
    ]);
    wrap(<DiagnosticsCard />);
    expect(await screen.findByText("Non configurato")).toBeInTheDocument();
    expect(screen.getByText("SIMULATO")).toBeInTheDocument();
    expect(screen.getByText("Da verificare live")).toBeInTheDocument();
    expect(screen.getByText("Test fallito")).toBeInTheDocument();
    expect(screen.getByText("Connesso")).toBeInTheDocument();
    expect(screen.getByText("Configurato")).toBeInTheDocument();
    expect(screen.getByText(/Chiave rifiutata/)).toBeInTheDocument();
  });

  it("richiede conferma esplicita prima di un test che scrive o costa, e non la manda se annullato", async () => {
    const calls = mockApi([entry({ id: "storage", label: "Storage R2/S3", state: "CONFIGURED", test_requires_confirm: true, confirm_text: "Carica un file di prova e lo cancella." })]);
    wrap(<DiagnosticsCard />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Testa" }));
    expect(screen.getByText("Carica un file di prova e lo cancella.")).toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST")).toBe(false);
    await user.click(screen.getByRole("button", { name: "Annulla" }));
    expect(calls.some((c) => c.method === "POST")).toBe(false);
    await user.click(screen.getByRole("button", { name: "Testa" }));
    await user.click(screen.getByRole("button", { name: "Conferma ed esegui" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toContain('"confirm":true'));
  });

  it("un test senza costi parte subito senza conferma", async () => {
    const calls = mockApi([entry({ id: "ffmpeg", label: "FFmpeg", state: "CONFIGURED" })]);
    wrap(<DiagnosticsCard />);
    await userEvent.setup().click(await screen.findByRole("button", { name: "Testa" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST")?.url).toContain("/diagnostics/ffmpeg/test"));
  });
});

describe("Stato remoto", () => {
  it("«Buffer segnala inviato» non si presenta come verificato su TikTok", () => {
    const { rerender } = render(<RemoteStateBadge state="BUFFER_REPORTS_SENT" />);
    expect(screen.getByText("Buffer segnala inviato")).toBeInTheDocument();
    expect(screen.queryByText("Verificato su TikTok")).toBeNull();
    rerender(<RemoteStateBadge state="EXTERNALLY_VERIFIED" />);
    expect(screen.getByText("Verificato su TikTok")).toBeInTheDocument();
    rerender(<RemoteStateBadge state="LOCAL" />);
    expect(screen.queryByText("Solo locale")).toBeNull();
  });
});
