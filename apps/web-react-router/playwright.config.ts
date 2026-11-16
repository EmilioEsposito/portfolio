import { defineConfig, devices } from "@playwright/test";

import { API_PORT, BASE_URL, WEB_PORT } from "./e2e/setup.ts";

// The booking page's browser test, against the local API and the built web
// app on a disposable database. See e2e/README.md.
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  timeout: 30_000,
  expect: { timeout: 10_000 },
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: BASE_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: "node e2e/serve.ts api",
      url: `http://127.0.0.1:${API_PORT}/api/health`,
      reuseExistingServer: false,
      timeout: 120_000,
      stdout: "pipe",
    },
    {
      command: "node e2e/serve.ts web",
      url: `http://127.0.0.1:${WEB_PORT}/privacy`,
      reuseExistingServer: false,
      timeout: 60_000,
      stdout: "pipe",
    },
  ],
});
