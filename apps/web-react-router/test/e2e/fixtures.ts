/**
 * What every browser test gets: a page that reaches nothing outside this
 * machine, with Cloudflare's Turnstile script answered by a stand-in, and
 * helpers that reset and read the test database.
 */
import { execFileSync } from "node:child_process";

import { test as base, type Page } from "@playwright/test";

import { PYTHON, REPO_ROOT, apiEnvironment } from "./setup.ts";

const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]", "serniacapital.localhost"]);
const TURNSTILE_SCRIPT = /^https:\/\/challenges\.cloudflare\.com\/turnstile\/v0\/api\.js/;

// Stands in for Cloudflare's api.js: each execute answers with a fresh token
// through the widget's callback, as a browser that passes the check would see.
const FAKE_TURNSTILE = `
window.__turnstile = { renders: [], executions: 0 };
window.turnstile = {
  render(container, options) {
    const id = "widget-" + (window.__turnstile.renders.push(options));
    return id;
  },
  execute(id) {
    const options = window.__turnstile.renders[Number(id.split("-")[1]) - 1];
    const n = ++window.__turnstile.executions;
    setTimeout(() => options.callback("browser-test-token-" + n), 20);
  },
  reset() {},
  remove() {},
};
`;

export type Rows = {
  showings: { id: number; starts_at: string; status: string }[];
  attendees: {
    showing_id: number;
    name: string;
    mobile: string | null;
    email: string | null;
    status: string;
    source: string;
    texts_accepted: boolean;
    requirement_confirmations: { key: string; wording: string; version: string }[];
    lead_token: string | null;
    mobile_corrections: number;
    user_agent: string | null;
  }[];
  notices: { kind: string; channel: string; status: string; recipient: string | null; body: string | null }[];
};

function database(command: "reset" | "rows"): unknown {
  const output = execFileSync(PYTHON, ["-m", "scripts.showing_browser_test", command], {
    cwd: REPO_ROOT,
    env: apiEnvironment(),
    encoding: "utf8",
  });
  // Logging may come first; the answer is the last line.
  const last = output.trim().split("\n").at(-1) ?? "null";
  return JSON.parse(last);
}

export const resetDatabase = () => database("reset");
export const readRows = () => database("rows") as Rows;

/** How many times the page ran the bot check. */
export function turnstileRuns(page: Page): Promise<number> {
  return page.evaluate(() => (window as unknown as { __turnstile?: { executions: number } }).__turnstile?.executions ?? 0);
}

export const test = base.extend<{ blocked: string[] }>({
  blocked: async ({}, use) => {
    await use([]);
  },
  page: async ({ page, blocked }, use, testInfo) => {
    await page.context().route("**/*", async (route) => {
      const url = new URL(route.request().url());
      if (LOCAL_HOSTS.has(url.hostname)) return route.continue();
      if (TURNSTILE_SCRIPT.test(url.href)) {
        return route.fulfill({ status: 200, contentType: "text/javascript", body: FAKE_TURNSTILE });
      }
      blocked.push(url.origin);
      return route.abort();
    });
    await use(page);
    if (blocked.length) {
      await testInfo.attach("blocked outside requests", { body: [...new Set(blocked)].join("\n"), contentType: "text/plain" });
    }
  },
});

export { expect } from "@playwright/test";
