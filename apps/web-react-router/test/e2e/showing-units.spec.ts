/**
 * The Sernia home page and the units list, end to end: from serniacapital.localhost's
 * home page to the list of units on offer to a unit's booking page, with no
 * Clerk on the way. Same servers and data as showing-booking.spec.ts.
 */
import { expect, resetDatabase, test } from "./fixtures.ts";
import { TEAM_PHONE, UNIT } from "./setup.ts";

test.beforeEach(() => {
  resetDatabase();
});

test("from the home page to the units list to a unit's booking page", async ({ page, blocked }) => {
  const response = await page.goto("/");
  expect(response?.status()).toBe(200);
  await expect(page).toHaveTitle("Sernia Capital");
  await expect(page.locator('link[rel="canonical"]')).toHaveAttribute("href", "https://serniacapital.com/");

  // The three links, in order, Resident sign-in first.
  const links = page.getByRole("navigation", { name: "Sernia Capital" }).getByRole("link");
  await expect(links).toHaveText([/^Resident sign-in/, /^Book a showing/, /^Apply/]);
  await expect(links.nth(0)).toHaveAttribute("href", "https://serniacapital.managebuilding.com/");
  await expect(links.nth(2)).toHaveAttribute("href", "/apply");
  await expect(page.getByText("Equal Housing Opportunity")).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Policies" }).getByRole("link")).toHaveText(["Privacy", "SMS Terms"]);
  // The call line is there, and the number is not in the page until it is asked for.
  await expect(page.getByRole("button", { name: "Show phone number" })).toBeVisible();
  expect(await page.content()).not.toContain(`${TEAM_PHONE.slice(2, 5)}-${TEAM_PHONE.slice(5, 8)}-${TEAM_PHONE.slice(8)}`);
  // No Clerk and nothing of the personal site.
  expect(await page.content()).not.toMatch(/clerk|Emilio Esposito/i);

  await links.nth(1).click();
  await expect(page).toHaveURL("/showings");
  await expect(page.getByRole("heading", { level: 1, name: "Book a showing" })).toBeVisible();
  await expect(page).toHaveTitle("Book a showing | Sernia Capital");
  const row = page.getByRole("list", { name: "Units for rent" }).getByRole("link", { name: /900 Example Ave, unit 900-01/ });
  await expect(row).toHaveAttribute("href", `/showings/${UNIT}`);
  await expect(row).toContainText("$1,450 a month · Lease starts between ");
  await expect(row).not.toContainText("No open times");

  await row.click();
  await expect(page).toHaveURL(`/showings/${UNIT}`);
  await expect(page.getByRole("heading", { level: 1, name: "Book an in-person showing" })).toBeVisible();
  await expect(page.getByText(`900 Example Ave, unit ${UNIT} · $1,450 a month · 30 minutes`)).toBeVisible();
  await expect(page.getByRole("group", { name: /^Times on / })).toBeVisible();
  // The only outside request any Sernia page makes is for its font.
  expect(blocked.filter((origin) => origin !== "https://fonts.googleapis.com")).toEqual([]);
});
