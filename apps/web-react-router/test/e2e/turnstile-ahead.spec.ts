/**
 * The bot check that runs ahead of time: the number shows without waiting for
 * a new check, and a check that needs the person shows nothing until they act.
 * Turnstile is the stand-in in fixtures.ts, as in the other browser tests.
 */
import type { Page } from "@playwright/test";

import { answerTurnstile, expect, resetDatabase, sentTokens, test, turnstileRuns } from "./fixtures.ts";
import { TEAM_PHONE, UNIT } from "./setup.ts";

const NUMBER = `${TEAM_PHONE.slice(2, 5)}-${TEAM_PHONE.slice(5, 8)}-${TEAM_PHONE.slice(8)}`;

const showNumber = (page: Page) => page.getByRole("button", { name: "Show phone number" });
const challenge = (page: Page) => page.getByText("Stand-in challenge");
const number = (page: Page) => page.getByRole("link", { name: NUMBER });

test.beforeEach(() => {
  resetDatabase();
});

test("the number shows without waiting for a new check, once the page has been idle", async ({ page }) => {
  const tokens = sentTokens(page);
  await page.goto("/");
  // Nobody has touched the page: the check runs because it is idle.
  await expect.poll(() => turnstileRuns(page)).toBe(1);
  expect(tokens).toEqual([]);
  expect(await page.content()).not.toContain(NUMBER);

  // A check started from here on would never answer.
  await answerTurnstile(page, "hang");
  // Hold the API response across idle callbacks to exercise the in-flight reveal.
  let releaseResponse!: () => void;
  const responseGate = new Promise<void>((resolve) => { releaseResponse = resolve; });
  await page.route("**/api/showing-bookings/public/team-phone", async (route) => {
    await responseGate;
    await route.continue();
  });
  await showNumber(page).click();
  try {
    await expect.poll(() => tokens.length).toBe(1);
    await page.evaluate(() => new Promise((resolve) => requestIdleCallback(() => requestIdleCallback(resolve))));
    expect(await turnstileRuns(page)).toBe(1);
  } finally {
    releaseResponse();
  }
  await expect(number(page)).toHaveAttribute("href", `tel:${TEAM_PHONE},1`);
  expect(tokens).toEqual(["browser-test-token-1"]);

  // The line has its number, so nothing on this page needs another token.
  await page.mouse.move(200, 200);
  await page.evaluate(() => new Promise((resolve) => requestIdleCallback(() => requestIdleCallback(resolve))));
  expect(await turnstileRuns(page)).toBe(1);
});

test("the call line and the booking form share the check, each with its own token", async ({ page }) => {
  const tokens = sentTokens(page);
  await page.goto(`/showings/${UNIT}`);
  await expect.poll(() => turnstileRuns(page)).toBe(1);

  await showNumber(page).click();
  await expect(number(page)).toBeVisible();
  // The next token is got ready for the form, which then waits for no check.
  await expect.poll(() => turnstileRuns(page)).toBe(2);
  await answerTurnstile(page, "hang");

  await page.getByRole("group", { name: /^Times on / }).getByRole("button").first().click();
  await page.getByLabel("Name").fill("Alex Parker");
  await page.getByLabel("Mobile").fill("412-555-0117");
  await page.getByLabel("Email").fill("alex@example.com");
  for (const box of await page.getByRole("checkbox").all()) await box.check();
  await page.getByRole("button", { name: "Book in-person showing" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Your showing is booked" })).toBeVisible();
  expect(tokens).toEqual(["browser-test-token-1", "browser-test-token-2"]);
});

test("a check that needs the person shows nothing until they act, then beside what they pressed", async ({ page }) => {
  await answerTurnstile(page, "ask");
  await page.goto("/");
  await expect.poll(() => turnstileRuns(page)).toBe(1);
  // It ran where no one can see or reach it, and was dropped when it needed someone.
  const backstage = page.locator("#turnstile-backstage");
  expect(await backstage.evaluate((el) => [getComputedStyle(el).opacity, (el as HTMLElement).inert])).toEqual(["0", true]);
  await expect(challenge(page)).toHaveCount(0);
  await page.mouse.move(200, 200);
  await expect(page.getByRole("alert")).toHaveCount(0);

  await showNumber(page).click();
  await expect(challenge(page)).toBeVisible();
  await expect(page.getByRole("main").getByText("Stand-in challenge")).toBeVisible();
  expect(await turnstileRuns(page)).toBe(2);
  await challenge(page).click();
  await expect(number(page)).toBeVisible();
  await expect(challenge(page)).toHaveCount(0);
});

test("a blocked check says nothing until the person asks, and works once it is not blocked", async ({ page }) => {
  let blocking = true;
  await page.route(/challenges\.cloudflare\.com/, (route) => (blocking ? route.abort() : route.fallback()));
  const failed = page.waitForEvent("requestfailed", (request) => request.url().includes("challenges.cloudflare.com"));
  await page.goto("/");
  await failed;
  await expect(page.getByRole("alert")).toHaveCount(0);

  await showNumber(page).click();
  await expect(page.getByRole("alert")).toHaveText("We couldn't run the check that you're not a bot. Try again.");
  blocking = false;
  await showNumber(page).click();
  await expect(number(page)).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
});
