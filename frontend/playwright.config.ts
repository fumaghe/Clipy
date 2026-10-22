import { defineConfig } from "@playwright/test";

// L'e2e avvia il backend reale (modalità demo, DB temporaneo) che serve anche la build del frontend.
export default defineConfig({
  testDir: "./e2e",
  timeout: 180_000,
  workers: 1,
  use: { baseURL: "http://127.0.0.1:8765", launchOptions: { executablePath: process.env.CLIPY_CHROMIUM ?? "/opt/pw-browsers/chromium-1194/chrome-linux/chrome" } },
  webServer: {
    command: "bash ../scripts/e2e-server.sh",
    url: "http://127.0.0.1:8765/health",
    reuseExistingServer: false,
    timeout: 60_000,
  },
});
