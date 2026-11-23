/**
 * The Sernia home page and the units list, end to end: from serniacapital.localhost's
 * home page to the list of units on offer to a unit's booking page, with no
 * Clerk on the way. Same servers and data as showing-booking.spec.ts.
 */
import { expect, resetDatabase, test } from "./fixtures.ts";
import { TEAM_PHONE, UNIT, WEB_PORT } from "./setup.ts";

const PORTAL = "https://serniacapital.managebuilding.com/";

test.beforeEach(() => {
  resetDatabase();
});

test("from the home page to the units list to a unit's booking page", async ({ page, blocked }) => {
  const response = await page.goto("/");
  expect(response?.status()).toBe(200);
  await expect(page).toHaveTitle("Sernia Capital | Apartments in Bloomfield and Shadyside");
  await expect(page.locator('link[rel="canonical"]')).toHaveAttribute("href", "https://serniacapital.com/");
  // Sernia's own icons, and not the app's install manifest.
  for (const icon of await page.locator('link[rel="icon"], link[rel="apple-touch-icon"]').all()) {
    await expect(icon).toHaveAttribute("href", /^\/images\/sernia\//);
  }
  await expect(page.locator('link[rel="manifest"]')).toHaveCount(0);

  // Residents sign in from the header; the rest is in the page.
  const header = page.getByRole("banner").getByRole("link");
  await expect(header).toHaveText(["Sernia Capital", "Resident sign-in"]);
  await expect(header.nth(0)).toHaveAttribute("href", "/");
  await expect(header.nth(1)).toHaveAttribute("href", PORTAL);
  const main = page.getByRole("main");
  await expect(main.getByRole("heading", { level: 1 })).toHaveText("Apartments in Bloomfield and Shadyside");
  const links = main.getByRole("link");
  await expect(links).toHaveText(["Read our Zillow reviews", "Book a showing", "Apply", "Resident sign-in"]);
  await expect(links.nth(0)).toHaveAttribute("href", "https://www.zillow.com/profile/Sernia%20Capital");
  await expect(links.nth(2)).toHaveAttribute("href", "/apply");
  await expect(links.nth(3)).toHaveAttribute("href", PORTAL);

  // The photo loads as the page reaches it.
  const photo = main.getByRole("img");
  await expect(photo).toHaveAttribute("alt", "A kitchen with an island and stainless steel appliances");
  await photo.scrollIntoViewIfNeeded();
  await expect.poll(() => photo.evaluate((image: HTMLImageElement) => image.naturalWidth)).toBeGreaterThan(0);

  const footer = page.getByRole("contentinfo");
  await expect(footer.getByText("Sernia Capital, Pittsburgh, PA")).toBeVisible();
  await expect(footer.getByText("Equal Housing Opportunity")).toBeVisible();
  await expect(footer.getByRole("link", { name: "Email us" })).toHaveAttribute("href", "mailto:all@serniacapital.com");
  const policies = page.getByRole("navigation", { name: "Policies" }).getByRole("link");
  await expect(policies).toHaveText(["Privacy"]);
  // The call line is there, and the number is not in the page until it is asked for.
  await expect(page.getByRole("button", { name: "Show phone number" })).toBeVisible();
  expect(await page.content()).not.toContain(`${TEAM_PHONE.slice(2, 5)}-${TEAM_PHONE.slice(5, 8)}-${TEAM_PHONE.slice(8)}`);
  // No Clerk and nothing of the personal site.
  expect(await page.content()).not.toMatch(/clerk|Emilio Esposito/i);

  await links.nth(1).click();
  await expect(page).toHaveURL("/showings");
  await expect(page.getByRole("heading", { level: 1, name: "Book a showing" })).toBeVisible();
  await expect(policies).toHaveText(["Privacy", "SMS Terms"]);
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

  // Only the home page has the sign-in button; the wordmark leads back to it.
  await expect(header).toHaveText(["Sernia Capital"]);
  await header.click();
  await expect(page).toHaveURL("/");
  await expect(header).toHaveText(["Sernia Capital", "Resident sign-in"]);
  // The only outside request any Sernia page makes is for its font.
  expect(blocked.filter((origin) => origin !== "https://fonts.googleapis.com")).toEqual([]);
});

test("a click shows at once, and a page load has the list in its HTML", async ({ page }) => {
  await page.goto("/");
  // Hold back the navigation's answer: the line across the top is there while it is out.
  let release = () => {};
  const held = new Promise<void>((resolve) => (release = resolve));
  await page.route("**/showings.data*", async (route) => {
    await held;
    await route.continue();
  });
  const line = page.getByRole("progressbar", { name: "Opening the page" });
  await expect(line).toHaveCount(0);
  await page.getByRole("main").getByRole("link", { name: "Book a showing" }).click();
  await expect(line).toBeVisible();
  release();
  await expect(page.getByRole("list", { name: "Units for rent" })).toBeVisible();
  await expect(line).toHaveCount(0);

  // A page load has the list in its HTML, for search engines and anything else without scripts.
  const response = await page.request.get(`http://localhost:${WEB_PORT}/showings`, { headers: { "user-agent": "Googlebot/2.1" } });
  expect(await response.text()).toContain("Lease starts between ");
});

test("the wordmark links to / only where / is the Sernia home page", async ({ page }) => {
  // A page no one can find is still on Sernia's host, with a home page to go to.
  const missing = await page.goto("/nothing-here");
  expect(missing?.status()).toBe(404);
  await expect(page.getByRole("banner").getByRole("link", { name: "Sernia Capital" })).toHaveAttribute("href", "/");

  // The same pages on another host, where / is another site's.
  await page.goto(`http://localhost:${WEB_PORT}/privacy`);
  await expect(page.getByRole("banner")).toHaveText("Sernia Capital");
  await expect(page.getByRole("banner").getByRole("link")).toHaveCount(0);
});

test("nothing on the home page is wider than the screen, in light or dark", async ({ page }) => {
  for (const colorScheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme });
    for (const width of [320, 390, 768, 1200, 1920]) {
      await page.setViewportSize({ width, height: 800 });
      await page.goto("/");
      // The drawing runs past both edges on purpose, inside an element that clips it.
      const past = await page
        .locator("body *:not([aria-hidden='true']):not([aria-hidden='true'] *)")
        .evaluateAll((elements, edge) => elements.filter((el) => el.getBoundingClientRect().right > edge + 0.5).length, width);
      expect(past, `${width} px, ${colorScheme}`).toBe(0);
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    }
  }
});
