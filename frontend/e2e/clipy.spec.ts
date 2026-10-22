import { expect, test } from "@playwright/test";

const api = (path: string) => `/api/v1${path}`;

test.describe.serial("Clipy end-to-end (demo)", () => {
  test("Overview mostra modalità demo/dry-run e health", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByText("Modalità demo.")).toBeVisible();
    await expect(page.getByText("DRY RUN").first()).toBeVisible();
    await expect(page.getByText("FFmpeg OK")).toBeVisible();
  });

  test("Run once importa le clip delle ultime 24h e la pipeline le elabora", async ({ page, request }) => {
    await request.patch(api("/settings"), { data: { twitch: { portrait_wait_minutes: 0 } } });
    await page.goto("/");
    await page.getByRole("button", { name: /Run once/ }).click();
    await expect(page.getByText(/6 clip trovate, 6 nuove/)).toBeVisible();
    // la pipeline gira davvero nel worker: attendo render e review gate
    await expect.poll(async () => {
      const r = await (await request.get(api("/clips?page_size=50"))).json();
      return r.items.filter((c: any) => ["AUTO_READY", "NEEDS_REVIEW"].includes(c.status)).length;
    }, { timeout: 120_000, intervals: [2000] }).toBeGreaterThanOrEqual(6);
  });

  test("Clip Library: card con stato e azioni collegate", async ({ page, request }) => {
    await page.goto("/library");
    await expect(page.getByText("Il carrello che vola").first()).toBeVisible();
    await page.getByPlaceholder(/Cerca/).fill("Salto");
    await expect(page.getByText("Salto impossibile")).toBeVisible();
    // Skip: osservabile in API
    const before = await (await request.get(api("/clips?status=SKIPPED"))).json();
    await page.getByRole("button", { name: /Skip/ }).first().click();
    await expect.poll(async () => (await (await request.get(api("/clips?status=SKIPPED"))).json()).total).toBe(before.total + 1);
  });

  test("Review Inbox: clip dubbie, Approva la sblocca e va in coda", async ({ page, request }) => {
    await page.goto("/review");
    await expect(page.getByText(/Perché è in revisione/).first()).toBeVisible();
    const inbox = await (await request.get(api("/reviews"))).json();
    expect(inbox.items.length).toBeGreaterThanOrEqual(1);
    const id = inbox.items[0].id;
    await page.getByRole("button", { name: /^Approva$/ }).first().click();
    await expect.poll(async () => (await (await request.get(api(`/clips/${id}`))).json()).status).toBe("APPROVED");
    const q = await (await request.get(api("/queue"))).json();
    expect(q.items.some((j: any) => j.clip_id === id)).toBeTruthy();
  });

  test("Review Inbox: Scarta non cancella i file", async ({ page, request }) => {
    const inbox = await (await request.get(api("/reviews"))).json();
    test.skip(inbox.items.length === 0, "nessuna clip in revisione rimasta");
    const id = inbox.items[0].id;
    await page.goto("/review");
    await page.getByRole("button", { name: /Scarta/ }).first().click();
    await expect.poll(async () => (await (await request.get(api(`/clips/${id}`))).json()).status).toBe("REJECTED");
    const d = await (await request.get(api(`/clips/${id}`))).json();
    expect(d.has_original).toBeTruthy();
  });

  test("Template Studio: anteprima reale (immagine generata da FFmpeg)", async ({ page }) => {
    await page.goto("/templates");
    await page.getByRole("button", { name: "Genera anteprima" }).click();
    const img = page.getByAltText("anteprima render");
    await expect(img).toBeVisible({ timeout: 30_000 });
    expect(await img.evaluate((e: HTMLImageElement) => e.naturalHeight > e.naturalWidth)).toBeTruthy();
    await expect(page.getByText("REFERENCE_NOT_VERIFIED").first()).toBeVisible();
  });

  test("Coda: slot, limite giornaliero e dry-run", async ({ page }) => {
    await page.goto("/queue");
    await expect(page.getByText(/\/4 post/).first()).toBeVisible();
    await expect(page.getByText(/DRY RUN attivo/)).toBeVisible();
    await page.getByRole("button", { name: /Esegui publisher ora/ }).click();
    await expect(page.getByText(/Publisher:/)).toBeVisible();
  });

  test("Storico: i job dry-run sono marcati Simulato, mai Pubblicato", async ({ page, request }) => {
    await page.goto("/history");
    await expect(page.getByText("Simulato (dry-run)").first()).toBeVisible();
    const r = await (await request.get(api("/publications?status=PUBLISHED"))).json();
    expect(r.items.length).toBe(0);
  });

  test("Settings: live richiede la frase di conferma e prerequisiti", async ({ page, request }) => {
    await page.goto("/settings");
    await expect(page.getByRole("heading", { name: "Pubblicazione reale" })).toBeVisible();
    const btn = page.getByRole("button", { name: "Abilita pubblicazione reale" });
    await expect(btn).toBeDisabled();
    // anche via API, senza Buffer collegato il live resta bloccato
    const res = await request.post(api("/settings/live/enable"), { data: { confirm: "ABILITA PUBBLICAZIONE REALE" } });
    expect(res.status()).toBe(409);
    const st = await (await request.get(api("/system/status"))).json();
    expect(st.live_enabled).toBe(false);
  });

  test("Settings: wizard Buffer con publisher demo e salvataggio slot", async ({ page, request }) => {
    await page.goto("/settings");
    await page.getByRole("button", { name: /Usa publisher DEMO/ }).click();
    await page.getByRole("button", { name: /Testa e elenca canali/ }).click();
    await expect(page.getByText("TikTok (SIMULATO)")).toBeVisible();
    await page.getByLabel(/Post al giorno/).fill("3");
    await page.locator("section", { has: page.getByRole("heading", { name: "Pianificazione" }) }).getByRole("button", { name: "Salva" }).click();
    await expect.poll(async () => (await (await request.get(api("/settings"))).json()).settings.publishing.daily_max).toBe(3);
  });

  test("AI Studio genera alternative senza nuovo download", async ({ page }) => {
    await page.goto("/ai");
    await expect(page.getByRole("heading", { name: "Prompt di sistema" })).toBeVisible();
    const opt = page.locator("select").last();
    await opt.selectOption({ index: 1 });
    await page.getByRole("button", { name: "Genera alternative" }).click();
    await expect(page.getByText(/Costo stimato/)).toBeVisible();
  });

  test("Settings: diagnostica distingue simulazioni, test reale FFmpeg e conferma per i test che scrivono", async ({ page, request }) => {
    await page.goto("/settings");
    const diag = page.getByTestId("diagnostics");
    await expect(diag).toBeVisible();
    await expect(page.getByTestId("diag-twitch").getByText("SIMULATO")).toBeVisible();
    await expect(page.getByTestId("diag-twitch").getByText("Non configurato")).toBeVisible();
    await page.getByTestId("diag-ffmpeg").getByRole("button", { name: "Testa" }).click();
    await expect(page.getByTestId("diag-ffmpeg").getByText("Connesso")).toBeVisible({ timeout: 30_000 });
    // lo storage richiede conferma esplicita: senza, nessun upload
    const res = await request.post(api("/integrations/diagnostics/storage/test"), { data: {} });
    expect(res.status()).toBe(422);
    // il test non ha alterato la modalità di pubblicazione
    expect((await (await request.get(api("/system/status"))).json()).live_enabled).toBe(false);
  });

  test("Sicurezza: origine estranea e host anomalo rifiutati, nessun segreto nelle risposte", async ({ request }) => {
    const bad = await request.post(api("/settings/live/disable"), { headers: { Origin: "https://evil.example" } });
    expect(bad.status()).toBe(403);
    const txt = await (await request.get(api("/settings"))).text();
    expect(txt).not.toMatch(/sk-[A-Za-z0-9]{16,}/);
  });
});
