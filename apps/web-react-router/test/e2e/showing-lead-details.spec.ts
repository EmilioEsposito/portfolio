/**
 * A showing the lead agent booked in a lead's email thread: the lead opens
 * their own link, sees the time already booked and adds their details; after
 * that, and after the showing moves by email, the link says they are booked.
 * Same set-up as showing-booking.spec.ts; the booking and the move are made by
 * scripts/showing_browser_test.py (lead-booking, lead-move), as a sent reply
 * makes them.
 */
import type { Page } from "@playwright/test";

import { formatWhen } from "../../app/sites/sernia-public/lib/showing-booking-page.ts";
import { bookByEmail, closeUnits, expect, moveByEmail, readRows, resetDatabase, test } from "./fixtures.ts";
import { UNIT } from "./setup.ts";

test.beforeEach(() => {
  resetDatabase();
});

/** The "You're booked" page: when and where, and nothing that could book, change or cancel. */
async function expectBooked(page: Page, token: string, startsAt: string) {
  const response = await page.goto(`/showings/${UNIT}?t=${token}`);
  expect(response?.status()).toBe(200);
  await expect(page.getByRole("heading", { level: 1, name: "You're booked" })).toBeVisible();
  await expect(page).toHaveTitle(/^You're booked to see unit 900-01/);
  const [day, time] = formatWhen(startsAt, "America/New_York").split(" at ");
  const facts = page.getByRole("main");
  await expect(facts).toContainText(day);
  await expect(facts).toContainText(time.replace(/ [AP]M$/, ""));
  await expect(facts).toContainText(`900 Example Ave, unit ${UNIT}`);
  await expect(facts).toContainText("The text we'll send you has a link to cancel this showing.");
  await expect(facts).toContainText("Didn't get the text? Call us.");
  // No form, and no cancel link or token anywhere in what the server sent.
  await expect(page.getByRole("button", { name: /Book|Add my details/ })).toHaveCount(0);
  await expect(page.getByRole("textbox")).toHaveCount(0);
  const html = (await response?.text()) ?? "";
  expect(html).not.toContain("cancel_token");
  expect(html).not.toContain("/showings/cancel/");
  for (const own of ["Jordan Lee", "4125550123", "jordan@example.com"]) expect(html).not.toContain(own);
}

test("a lead adds their details to the showing booked by email", async ({ page }) => {
  const { token, starts_at } = bookByEmail();
  const before = readRows();
  expect(before.attendees).toHaveLength(1);
  expect(before.attendees[0]).toMatchObject({ source: "lead_agent", texts_accepted: false, email: null });

  const response = await page.goto(`/showings/${UNIT}?t=${token}`);
  expect(response?.status()).toBe(200);
  await expect(page.getByRole("heading", { level: 1, name: "Add your details" })).toBeVisible();
  const when = formatWhen(starts_at, "America/New_York");
  const [day, time] = when.split(" at ");
  const booked = page.getByRole("region", { name: "Your showing" });
  await expect(booked).toContainText(day);
  await expect(booked).toContainText(time);
  await expect(booked).toContainText("booked");
  await expect(page.getByRole("heading", { name: "Pick a time" })).toHaveCount(0);
  // The token never reaches an API path or query string.
  expect(page.url()).toContain(`?t=${token}`);

  await page.getByLabel("Name").fill("Jordan Lee");
  await page.getByLabel("Mobile").fill("412-555-0123");
  await page.getByLabel("Email").fill("jordan@example.com");
  const ticks = page.getByRole("group", { name: "To book online, tick each box" }).getByRole("checkbox");
  for (const box of await ticks.all()) await box.check();
  const requests: string[] = [];
  page.on("request", (request) => requests.push(request.url()));
  await page.getByRole("button", { name: "Add my details" }).click();

  await expect(page.getByRole("heading", { level: 1, name: "Your showing is booked" })).toBeFocused();
  await expect(page.getByText("We'll text •••-•••-••23.")).toBeVisible();
  expect(requests.filter((url) => url.includes("/api/")).every((url) => !url.includes(token))).toBe(true);

  const rows = readRows();
  expect(rows.showings).toHaveLength(1);
  expect(rows.attendees).toHaveLength(1);
  expect(rows.attendees[0]).toMatchObject({
    name: "Jordan Lee",
    mobile: "+14125550123",
    email: "jordan@example.com",
    source: "lead_agent",
    texts_accepted: true,
    lead_token: token,
  });
  const kinds = rows.notices.map((n) => [n.kind, n.status]);
  expect(kinds).toContainEqual(["prospect_confirmation", "captured"]);
  expect(kinds).toContainEqual(["prospect_invite", "held"]);
  expect(rows.notices.filter((n) => n.kind === "host_event")).toHaveLength(1);

  // The link now says they are booked: the details are in.
  await expectBooked(page, token, starts_at);
});

test("a lead who added their details sees they are booked, at the new time after a move by email", async ({ page }) => {
  const { token, starts_at } = bookByEmail();
  await page.goto(`/showings/${UNIT}?t=${token}`);
  await page.getByLabel("Name").fill("Jordan Lee");
  await page.getByLabel("Mobile").fill("412-555-0123");
  await page.getByLabel("Email").fill("jordan@example.com");
  const ticks = page.getByRole("group", { name: "To book online, tick each box" }).getByRole("checkbox");
  for (const box of await ticks.all()) await box.check();
  await page.getByRole("button", { name: "Add my details" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Your showing is booked" })).toBeFocused();
  await expectBooked(page, token, starts_at);

  const moved = moveByEmail();
  expect(moved.token).toBe(token);
  expect(moved.starts_at).not.toBe(starts_at);
  await expectBooked(page, token, moved.starts_at);

  // The new place kept the details, and only its own confirmation text says so.
  const rows = readRows();
  expect(rows.attendees.map((a) => a.status)).toEqual(["cancelled", "booked"]);
  expect(rows.attendees[1]).toMatchObject({
    name: "Jordan Lee",
    mobile: "+14125550123",
    email: "jordan@example.com",
    source: "lead_agent",
    texts_accepted: true,
  });
  expect(rows.attendees[1].requirement_confirmations).toEqual(rows.attendees[0].requirement_confirmations);
  expect(rows.notices.map((n) => n.kind)).not.toContain("prospect_cancelled");
  const confirmations = rows.notices.filter((n) => n.kind === "prospect_confirmation");
  expect(confirmations.map((n) => n.recipient)).toEqual(["+14125550123", "+14125550123"]);
  expect(confirmations[1].body).toContain(`has moved to ${formatWhen(moved.starts_at, "America/New_York")}`);
  expect(confirmations[1].body).toContain(`It replaces your showing on ${formatWhen(starts_at, "America/New_York")}.`);

  // Another thread's link, or none, still opens the normal booking page.
  await page.goto(`/showings/${UNIT}?t=${"x".repeat(22)}`);
  await expect(page.getByRole("heading", { level: 1, name: "Book an in-person showing" })).toBeVisible();
});

test("a lead's own link works for a unit that is no longer open to others", async ({ page }) => {
  const { token } = bookByEmail();
  closeUnits();

  const others = await page.goto(`/showings/${UNIT}`);
  expect(others?.status()).toBe(404);
  await expect(page.getByRole("heading", { level: 1, name: "This unit is not open for showings" })).toBeVisible();
  await page.goto("/showings");
  await expect(page.getByRole("link", { name: new RegExp(UNIT) })).toHaveCount(0);

  const own = await page.goto(`/showings/${UNIT}?t=${token}`);
  expect(own?.status()).toBe(200);
  await expect(page.getByRole("heading", { level: 1, name: "Add your details" })).toBeVisible();
  await page.getByLabel("Name").fill("Jordan Lee");
  await page.getByLabel("Mobile").fill("412-555-0123");
  await page.getByLabel("Email").fill("jordan@example.com");
  const ticks = page.getByRole("group", { name: "To book online, tick each box" }).getByRole("checkbox");
  for (const box of await ticks.all()) await box.check();
  await page.getByRole("button", { name: "Add my details" }).click();

  await expect(page.getByRole("heading", { level: 1, name: "Your showing is booked" })).toBeFocused();
  expect(readRows().attendees[0]).toMatchObject({ mobile: "+14125550123", status: "booked" });
});
