/**
 * A showing the lead agent booked in a lead's email thread: the lead opens
 * their own link, sees the time already booked and adds their details. Same
 * set-up as showing-booking.spec.ts; the booking itself is made by
 * scripts/showing_browser_test.py lead-booking, as a sent reply makes it.
 */
import { formatWhen } from "../../app/lib/showing-booking-page.ts";
import { bookByEmail, expect, readRows, resetDatabase, test } from "./fixtures.ts";
import { UNIT } from "./setup.ts";

test.beforeEach(() => {
  resetDatabase();
});

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
  await expect(page.getByText("We texted •••-•••-••23.")).toBeVisible();
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

  // The link now opens the normal booking page: the details are in.
  await page.goto(`/showings/${UNIT}?t=${token}`);
  await expect(page.getByRole("heading", { level: 1, name: "Book an in-person showing" })).toBeVisible();
});
