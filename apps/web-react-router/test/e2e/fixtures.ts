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

/**
 * How the stand-in for Turnstile answers a check: `pass` with a fresh token at
 * once, as most browsers get; `ask` puts a button in the widget's container,
 * as a check that needs the person does, and passes when it is pressed; `hang`
 * never answers.
 */
export type TurnstileAnswer = "pass" | "ask" | "hang";

// Stands in for Cloudflare's api.js.
const FAKE_TURNSTILE = `
window.__turnstile = { widgets: [], executions: 0, answer: window.__turnstileAnswer ?? "pass" };
window.turnstile = {
  render(container, options) {
    return "widget-" + window.__turnstile.widgets.push({ container, options });
  },
  execute(id) {
    const widget = window.__turnstile.widgets[Number(id.split("-")[1]) - 1];
    const n = ++window.__turnstile.executions;
    const pass = () => widget.options.callback("browser-test-token-" + n);
    const answer = window.__turnstile.answer;
    if (answer === "hang") return;
    setTimeout(() => {
      if (answer === "pass") return pass();
      widget.options["before-interactive-callback"]?.();
      widget.challenge = document.createElement("button");
      widget.challenge.type = "button";
      widget.challenge.textContent = "Stand-in challenge";
      widget.challenge.onclick = pass;
      widget.container.append(widget.challenge);
    }, 20);
  },
  remove(id) {
    window.__turnstile.widgets[Number(id.split("-")[1]) - 1].challenge?.remove();
  },
};
`;

export type Rows = {
  showings: { id: number; starts_at: string; status: string; cancel_reason: string | null }[];
  attendees: {
    showing_id: number;
    name: string;
    mobile: string | null;
    email: string | null;
    status: string;
    cancel_reason: string | null;
    source: string;
    texts_accepted: boolean;
    requirement_confirmations: { key: string; wording: string; version: string }[];
    lead_token: string | null;
    mobile_corrections: number;
    user_agent: string | null;
  }[];
  notices: {
    kind: string;
    channel: string;
    status: string;
    recipient: string | null;
    /** The other numbers of a group text. */
    also_to: string[];
    body: string | null;
  }[];
};

function database(command: "reset" | "rows" | "lead-booking" | "lead-move"): unknown {
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
/** A made-up Zillow thread booked into the unit by email, as a sent reply does: its link token and the start. */
export const bookByEmail = () => database("lead-booking") as { token: string; starts_at: string };
/** That thread's showing moved to another open time, as a sent reply does: the same token and the new start. */
export const moveByEmail = () => database("lead-move") as { token: string; starts_at: string };

type TurnstileWindow = { __turnstile?: { executions: number; answer: TurnstileAnswer }; __turnstileAnswer?: TurnstileAnswer };

/** How many times the page ran the bot check, ahead of time or for a person. */
export function turnstileRuns(page: Page): Promise<number> {
  return page.evaluate(() => (window as unknown as TurnstileWindow).__turnstile?.executions ?? 0);
}

/** How the stand-in answers from now on, on this page and any it opens later. */
export async function answerTurnstile(page: Page, answer: TurnstileAnswer) {
  const set = (value: TurnstileAnswer) => {
    const target = window as unknown as TurnstileWindow;
    target.__turnstileAnswer = value;
    if (target.__turnstile) target.__turnstile.answer = value;
  };
  await page.addInitScript(set, answer);
  await page.evaluate(set, answer);
}

/** The Turnstile tokens the page sends to the API, as it sends them. */
export function sentTokens(page: Page): string[] {
  const tokens: string[] = [];
  page.on("request", (request) => {
    if (request.method() !== "POST" || !request.url().includes("/api/showing-bookings/public/")) return;
    const token = (request.postDataJSON() as { turnstile_token?: string } | null)?.turnstile_token;
    if (token) tokens.push(token);
  });
  return tokens;
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
