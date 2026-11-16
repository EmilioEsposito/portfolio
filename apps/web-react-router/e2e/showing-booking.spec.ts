/**
 * The public booking page, end to end: the built web app on a Sernia host,
 * the real API and a disposable Postgres in capture mode. Turnstile is a
 * stand-in in the browser and Cloudflare's test secret on the server; Quo and
 * Google are never called. Each test starts from scripts/showing_browser_test.py
 * reset and checks the rows the page left.
 */
import type { Page } from "@playwright/test";

import { formatDay, formatWhen } from "../app/lib/showing-booking-page.ts";
import { expect, readRows, resetDatabase, test, turnstileRuns } from "./fixtures.ts";
import { TEAM_PHONE, UNIT } from "./setup.ts";

const PAGE = `/showings/${UNIT}`;
const PETS = "No pet dogs will live in the apartment.";
const TEXTS = "Text me about this showing and my rental inquiry.";

test.beforeEach(() => {
  resetDatabase();
});

/** Picks the first open time on the first day and answers what the page showed. */
async function pickFirstTime(page: Page): Promise<string> {
  const times = page.getByRole("group", { name: /^Times on / });
  const day = ((await times.getAttribute("aria-label")) ?? "").replace("Times on ", "");
  const first = times.getByRole("button").first();
  const time = ((await first.textContent()) ?? "").match(/\d{1,2}:\d{2} [AP]M/)?.[0] ?? "";
  await first.click();
  await expect(page.getByRole("button", { name: /^Change time/ })).toBeFocused();
  return `${day} at ${time}`;
}

async function fillDetails(page: Page, details: { name: string; mobile: string; email: string }) {
  await page.getByLabel("Name").fill(details.name);
  await page.getByLabel("Mobile").fill(details.mobile);
  await page.getByLabel("Email").fill(details.email);
}

function ticks(page: Page) {
  return page.getByRole("group", { name: "To book online, tick each box" }).getByRole("checkbox");
}

test("book a showing through to the confirmation screen", async ({ page }) => {
  const response = await page.goto(`${PAGE}?t=lead-token-123`);
  expect(response?.status()).toBe(200);
  await expect(page.getByRole("heading", { level: 1, name: "Book an in-person showing" })).toBeVisible();
  await expect(page.getByText(`Unit ${UNIT} · $1,450 a month · 30 minutes`)).toBeVisible();
  await expect(page.locator('meta[name="robots"]')).toHaveAttribute("content", "noindex");

  // The team's number is not in the page until the bot check passes.
  const display = `${TEAM_PHONE.slice(2, 5)}-${TEAM_PHONE.slice(5, 8)}-${TEAM_PHONE.slice(8)}`;
  expect(await page.content()).not.toContain(display);
  await page.getByRole("button", { name: "Show phone number" }).click();
  await expect(page.getByRole("link", { name: display })).toHaveAttribute("href", `tel:${TEAM_PHONE},1`);
  await expect(page.getByText("(extension 1) instead.")).toBeVisible();

  // The picker opens on the day of the API's best offer.
  const unit = await page.evaluate(async (path) => (await fetch(path)).json(), `/api/showing-bookings/public/units/${UNIT}`);
  await expect(page.getByRole("group", { name: `Times on ${formatDay(unit.offers[0].start, unit.timezone)}` })).toBeVisible();

  const picked = await pickFirstTime(page);
  await fillDetails(page, { name: "Alex Parker", mobile: "412-555-0117", email: "alex@example.com" });
  const wording = await ticks(page).evaluateAll((boxes) => boxes.map((box) => box.closest("label")?.textContent ?? ""));
  expect(wording.slice(1)).toEqual([PETS, TEXTS]);
  expect(wording[0]).toMatch(/^My lease can start between /);
  for (const box of await ticks(page).all()) await box.check();
  const smallPrint = page.getByRole("group", { name: "To book online, tick each box" });
  await expect(smallPrint.getByRole("link", { name: "Privacy Policy" })).toHaveAttribute("href", "/privacy");
  await expect(smallPrint.getByRole("link", { name: "SMS Terms" })).toHaveAttribute("href", "/sms-terms");

  await page.getByRole("button", { name: "Book in-person showing" }).click();

  await expect(page.getByRole("heading", { level: 1, name: "Your showing is booked" })).toBeFocused();
  await expect(page.getByText("900 Example Ave, unit 900-01")).toBeVisible();
  await expect(page.getByText("We texted •••-•••-••17.")).toBeVisible();
  await expect(page.getByText("The text has a link to cancel if your plans change.")).toBeVisible();
  await expect(page.getByRole("link", { name: /cancel/i })).toHaveCount(0);
  // One check for the number, one for the booking: a fresh token each time.
  expect(await turnstileRuns(page)).toBe(2);

  const rows = readRows();
  expect(rows.showings).toHaveLength(1);
  const [showing] = rows.showings;
  expect(showing.status).toBe("scheduled");
  expect(formatWhen(showing.starts_at, "America/New_York")).toBe(picked);
  await expect(page.getByText(picked.replace(" at ", ", ").replace(/ [AP]M$/, ""))).toBeVisible();

  expect(rows.attendees).toHaveLength(1);
  const [alex] = rows.attendees;
  expect(alex).toMatchObject({
    showing_id: showing.id,
    name: "Alex Parker",
    mobile: "+14125550117",
    email: "alex@example.com",
    status: "booked",
    source: "page",
    texts_accepted: true,
    lead_token: "lead-token-123",
    mobile_corrections: 0,
  });
  expect(alex.user_agent).toContain("Chrome");
  // The ticks are saved with the wording the person saw.
  expect(alex.requirement_confirmations.map((c) => [c.key, c.wording])).toEqual([
    ["lease_start", wording[0]],
    ["pets", PETS],
  ]);

  expect(rows.notices.map((n) => [n.kind, n.status])).toEqual([
    ["prospect_confirmation", "captured"],
    ["host_event", "held"],
    ["prospect_invite", "held"],
  ]);
  const text = rows.notices[0];
  expect(text.recipient).toBe("+14125550117");
  expect(text.body).toContain(`Sernia Capital: your in-person showing is booked for ${picked}`);
  expect(text.body).toMatch(/To cancel: https:\/\/serniacapital\.com\/showings\/cancel\/\S+/);
});

test("a missed tick is marked and nothing is booked until it is ticked", async ({ page }) => {
  await page.goto(PAGE);
  await pickFirstTime(page);
  await fillDetails(page, { name: "Robin Lee", mobile: "412-555-0118", email: "robin@example.com" });
  const pets = page.getByRole("checkbox", { name: PETS });
  await page.getByRole("checkbox", { name: /^My lease can start/ }).check();
  await page.getByRole("checkbox", { name: TEXTS }).check();

  await page.getByRole("button", { name: "Book in-person showing" }).click();

  await expect(page.getByRole("status").filter({ hasText: "One box still needs a tick. It is marked above." })).toBeVisible();
  await expect(pets).toBeFocused();
  await expect(pets).toHaveAttribute("aria-invalid", "true");
  await expect(pets).toHaveAccessibleDescription("Tick this box to book online.");
  expect(await turnstileRuns(page)).toBe(0);
  expect(readRows().showings).toEqual([]);

  // What was typed is still there; ticking the box is all it takes.
  await expect(page.getByLabel("Name")).toHaveValue("Robin Lee");
  await pets.check();
  await expect(page.getByText("Tick this box to book online.")).toHaveCount(0);
  await page.getByRole("button", { name: "Book in-person showing" }).click();
  await expect(page.getByRole("heading", { name: "Your showing is booked" })).toBeVisible();
  expect(readRows().attendees.map((a) => a.email)).toEqual(["robin@example.com"]);
});

test("correct the mobile from the confirmation screen, twice at most", async ({ page }) => {
  await page.goto(PAGE);
  await pickFirstTime(page);
  await fillDetails(page, { name: "Casey Moore", mobile: "(412) 555-0118", email: "casey@example.com" });
  for (const box of await ticks(page).all()) await box.check();
  await page.getByRole("button", { name: "Book in-person showing" }).click();
  await expect(page.getByText("We texted •••-•••-••18.")).toBeVisible();

  await page.getByRole("button", { name: "Fix it" }).click();
  const mobile = page.getByLabel("Your mobile");
  await expect(mobile).toBeFocused();
  // The same number is refused beside the field.
  await mobile.fill("412-555-0118");
  await page.getByRole("button", { name: "Text this number" }).click();
  await expect(page.getByText("Enter a different number.")).toBeVisible();
  await expect(mobile).toHaveAttribute("aria-invalid", "true");

  await mobile.fill("412-555-0119");
  await page.getByRole("button", { name: "Text this number" }).click();
  await expect(page.getByText("We texted •••-•••-••19.")).toBeVisible();
  await expect(page.getByRole("status")).toHaveText("Done. We texted the confirmation to •••-•••-••19.");

  let rows = readRows();
  expect(rows.attendees.map((a) => [a.mobile, a.mobile_corrections])).toEqual([["+14125550119", 1]]);
  expect(rows.notices.filter((n) => n.kind === "prospect_confirmation").map((n) => [n.recipient, n.status])).toEqual([
    ["+14125550118", "captured"],
    ["+14125550119", "captured"],
  ]);

  // The second correction is the last one the page offers.
  await page.getByRole("button", { name: "Fix it" }).click();
  await page.getByLabel("Your mobile").fill("412-555-0120");
  await page.getByRole("button", { name: "Text this number" }).click();
  await expect(page.getByText("We texted •••-•••-••20.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Fix it" })).toHaveCount(0);
  await expect(page.getByText("To change the number again, call us.")).toBeVisible();
  rows = readRows();
  expect(rows.attendees.map((a) => [a.mobile, a.mobile_corrections])).toEqual([["+14125550120", 2]]);
});

test("a unit that is not on offer answers 404 with a way forward", async ({ page }) => {
  const response = await page.goto("/showings/999-99");
  expect(response?.status()).toBe(404);
  await expect(page.getByRole("heading", { level: 1, name: "This unit is not open for showings" })).toBeVisible();
  await expect(page.getByRole("link", { name: "all@serniacapital.com" })).toBeVisible();
});
