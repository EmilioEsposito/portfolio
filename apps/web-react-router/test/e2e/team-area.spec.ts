/**
 * The team's pages at /team, signed out, and the sites that load no Clerk.
 * The web server has made-up Clerk keys (setup.ts), so no one can sign in:
 * what a signed-in team member sees is not covered here.
 */
import type { Page } from "@playwright/test";

import { expect, test } from "./fixtures.ts";
import { CLERK_HOST, WEB_PORT } from "./setup.ts";

const PORTFOLIO = `http://localhost:${WEB_PORT}`;

/** Every address the page asks for from now on, whether it is answered or blocked. */
function requested(page: Page): string[] {
  const urls: string[] = [];
  page.on("request", (request) => urls.push(request.url()));
  return urls;
}

const clerkRequests = (urls: string[]) => urls.filter((url) => /clerk/i.test(new URL(url).hostname));

test("/team signed out on the Sernia host is the team's sign-in page", async ({ page, blocked }) => {
  const urls = requested(page);
  const response = await page.goto("/team");
  expect(response?.status()).toBe(200);
  await expect(page).toHaveTitle("Sernia Capital team");
  await expect(page.locator('meta[name="robots"]')).toHaveAttribute("content", "noindex, nofollow");
  await expect(page.locator('link[rel="manifest"]')).toHaveAttribute("href", "/team/manifest.json");

  const main = page.getByRole("main");
  await expect(main.getByText("Sernia Capital", { exact: true })).toBeVisible();
  await expect(main.getByRole("heading", { level: 1 })).toHaveText("This area is for the Sernia Capital team.");
  const signIn = main.getByRole("button", { name: "Sign in" });
  await expect(signIn).toBeEnabled();
  // No team page shows before sign-in.
  await expect(page.getByRole("link", { name: "Team home" })).toHaveCount(0);

  // The click shows at once that sign-in is on its way, though Clerk's script
  // (blocked here) has not loaded.
  await signIn.click();
  await expect(main.getByRole("button", { name: "Opening sign-in…" })).toBeDisabled();

  // The team's pages are the ones that load Clerk: its script was asked for.
  expect(clerkRequests(urls).length).toBeGreaterThan(0);
  expect(blocked).toContain(`https://${CLERK_HOST}`);
});

test("every team page is behind the same sign-in, and an unknown team page is the team's 404", async ({ page }) => {
  for (const path of ["/team/chat?id=conversation-1", "/team/leads", "/team/leads/bookings", "/team/tenants", "/team/hitl-queue"]) {
    const response = await page.goto(path);
    expect(response?.status(), path).toBe(200);
    await expect(page.getByRole("heading", { level: 1 }), path).toHaveText("This area is for the Sernia Capital team.");
  }
  const missing = await page.goto("/team/nothing-here");
  expect(missing?.status()).toBe(404);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Page not found");
  await expect(page.getByRole("link", { name: "Go to the team home" })).toHaveAttribute("href", "/team");
});

test("the Sernia home page links to the team in its header and footer", async ({ page }) => {
  const response = await page.goto("/");
  expect(response?.status()).toBe(200);
  await expect(page.getByRole("banner").getByRole("link", { name: "Team", exact: true })).toHaveAttribute("href", "/team");
  await expect(page.getByRole("contentinfo").getByRole("link", { name: "Team", exact: true })).toHaveAttribute("href", "/team");

  // Following it opens the team's sign-in page.
  await page.getByRole("banner").getByRole("link", { name: "Team", exact: true }).click();
  await expect(page).toHaveURL(/\/team$/);
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
});

test("a portfolio page and the Sernia public pages make no request to Clerk", async ({ page }) => {
  const urls = requested(page);
  for (const url of ["/", "/privacy", "/showings", `${PORTFOLIO}/`, `${PORTFOLIO}/systems/leasing-leads`, `${PORTFOLIO}/privacy`]) {
    const response = await page.goto(url);
    expect(response?.status(), url).toBe(200);
    await page.waitForLoadState("networkidle");
    await expect(page.locator('script[src*="clerk"]'), url).toHaveCount(0);
    expect(await page.evaluate(() => "Clerk" in window), url).toBe(false);
  }
  expect(urls.length).toBeGreaterThan(6);
  expect(clerkRequests(urls)).toEqual([]);
});

test("the portfolio's /systems/leasing-leads shows the diagram, with no staff tabs", async ({ page }) => {
  const response = await page.goto(`${PORTFOLIO}/systems/leasing-leads`);
  expect(response?.status()).toBe(200);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Zillow Leads Agent");
  await expect(page.getByRole("figure", { name: "Leasing architecture" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Who hears what, and when" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "What is left to people" })).toBeVisible();
  // No tabs, and no link to a staff page other than the team site itself.
  await expect(page.getByRole("navigation", { name: /Lead agent pages|Zillow Leads Agent pages/ })).toHaveCount(0);
  const hrefs = await page.locator("a[href]").evaluateAll((links) => links.map((link) => link.getAttribute("href") ?? ""));
  expect(hrefs.filter((href) => /^\/(team|sernia-chat|sernia-settings|sernia-admin|message-tenants|scheduler|hitl-agent)/.test(href))).toEqual([]);
  expect(hrefs).toContain("https://serniacapital.com/team");
  // No install manifest or home-screen app tags outside the team's pages.
  await expect(page.locator('link[rel="manifest"]')).toHaveCount(0);
  await expect(page.locator('meta[name^="apple-mobile-web-app"]')).toHaveCount(0);
});

test("the old staff addresses are gone, and the Sernia host forwards only the team's and the public API", async ({ page }) => {
  for (const path of ["/sernia-chat", "/sernia-settings", "/sernia-admin", "/message-tenants", "/scheduler", "/hitl-agent-chat", "/systems/leasing-leads/bookings"]) {
    const response = await page.request.get(`${PORTFOLIO}${path}`);
    expect(response.status(), path).toBe(404);
  }
  // Asked from a page on the Sernia host, as the team's pages ask.
  await page.goto("/privacy");
  const status = (path: string) => page.evaluate(async (url) => (await fetch(url)).status, path);
  // Forwarded: the API itself answers, refusing a request with no sign-in.
  expect([401, 403]).toContain(await status("/api/sernia-ai/conversations/history?limit=1"));
  // Refused by the web server before it reaches the API, which would serve this page.
  expect(await status("/api/docs")).toBe(404);
  expect((await page.request.get(`${PORTFOLIO}/api/docs`)).status()).toBe(200);
  // robots.txt keeps crawlers out of /team on the Sernia host.
  const robots = await page.evaluate(async () => (await fetch("/robots.txt")).text());
  expect(robots).toContain("Disallow: /team");
});
