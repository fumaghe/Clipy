import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Thumb, Video } from "../components/ClipMedia";
import { ToastProvider } from "../components/ui";
import { Library } from "../pages/Library";

const clip = (over = {}) => ({
  id: "c1", source_clip_id: "s1", title: "Clip", game_name: "GTA", creator_name: "x", twitch_url: "https://t.tv/x", created_at_utc: "2026-01-01T00:00:00Z", duration: 20,
  status: "AUTO_READY", error: null, is_demo: true, demo_scenario: "", portrait_available: true, is_portrait_file: true, landscape_fallback: false,
  overlay_title: "Titolo", caption: "Cap", hashtags: [], template_id: null, content_version: 3, paused: false, priority: 0, has_original: true, has_render: true,
  transcript_excerpt: "", confidence: 0.9, analysis_simulated: false, review_state: null, review_reasons: [], publication_status: null, publication_due_at: null,
  busy: false, busy_reason: null, pending: false, ...over,
});

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><ToastProvider><MemoryRouter>{ui}</MemoryRouter></ToastProvider></QueryClientProvider>);
}

afterEach(() => vi.unstubAllGlobals());

describe("anteprime e player", () => {
  it("non richiede alcuna anteprima finché il file non esiste e mostra lo stato", () => {
    const { container } = render(<Thumb clip={clip({ status: "WAITING_FOR_MEDIA", has_original: false, has_render: false })} />);
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText("Twitch sta preparando il file")).toBeInTheDocument();
  });

  it("usa l'originale finché il render non c'è, poi passa al render con versione per invalidare la cache", () => {
    const { container, rerender } = render(<Thumb clip={clip({ has_render: false, status: "RENDERING", busy_reason: "Render in corso" })} />);
    expect(container.querySelector("img")!.getAttribute("src")).toBe("/api/v1/clips/c1/preview?kind=original&v=3");
    expect(screen.getByText("Render in corso")).toBeInTheDocument();
    rerender(<Thumb clip={clip({ content_version: 4 })} />);
    expect(container.querySelector("img")!.getAttribute("src")).toBe("/api/v1/clips/c1/preview?kind=render&v=4");
  });

  it("un errore di caricamento non resta «incollato» quando l'URL cambia", () => {
    const { container, rerender } = render(<Thumb clip={clip()} />);
    container.querySelector("img")!.dispatchEvent(new Event("error"));
    return waitFor(() => expect(screen.getByText("Anteprima non disponibile")).toBeInTheDocument()).then(() => {
      rerender(<Thumb clip={clip({ content_version: 9 })} />);
      expect(container.querySelector("img")).not.toBeNull();
    });
  });

  it("il <video> (e le richieste Range) nasce solo al click e rilascia lo stream allo smontaggio", async () => {
    const { container, unmount } = render(<Video id="c1" kind="render" version={3} />);
    expect(container.querySelector("video")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: /riproduci render/i }));
    const v = container.querySelector("video")!;
    expect(v.getAttribute("src")).toBe("/api/v1/clips/c1/video?kind=render&v=3");
    vi.spyOn(v, "pause").mockImplementation(() => {});
    const load = vi.spyOn(v, "load").mockImplementation(() => {});
    unmount();
    expect(load).toHaveBeenCalled();
    expect(v.hasAttribute("src")).toBe(false);
  });
});

describe("Library: clip occupata", () => {
  it("disattiva i comandi incompatibili e spiega perché; un 409 è un avviso, non un errore", async () => {
    const calls: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      calls.push(`${init?.method ?? "GET"} ${url}`);
      if (url.startsWith("/api/v1/clips?")) {
        return new Response(JSON.stringify({ items: [clip({ id: "busy", status: "RENDERING", busy: true, busy_reason: "Render in corso" }), clip({ id: "free" })], total: 2, page: 1, page_size: 18 }));
      }
      return new Response(JSON.stringify({ error: { code: "busy", message: "Render in corso: attendi che finisca e riprova" } }), { status: 409 });
    }));
    wrap(<Library />);
    await waitFor(() => expect(screen.getAllByRole("button", { name: /analyze/i })).toHaveLength(2));
    const [busyAnalyze, freeAnalyze] = screen.getAllByRole("button", { name: /analyze/i });
    const [busyRender, freeRender] = screen.getAllByRole("button", { name: /render$/i });
    expect(busyAnalyze).toBeDisabled();
    expect(busyRender).toBeDisabled();
    expect(busyRender).toHaveAttribute("title", "Render in corso: attendi che finisca");
    expect(screen.getByRole("status")).toHaveTextContent("Render in corso");
    expect(freeAnalyze).toBeEnabled();
    await userEvent.click(freeRender); // il backend risponde 409 (es. render partito da un'altra scheda)
    expect(await screen.findByText("Render in corso: attendi che finisca e riprova")).toHaveClass("text-amber-100");
    expect(calls).toContain("POST /api/v1/clips/free/render");
  });
});
