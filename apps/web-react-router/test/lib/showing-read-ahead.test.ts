import assert from "node:assert/strict";
import test from "node:test";

import { forgetReadAhead, readAhead, takeReadAhead } from "~/lib/read-ahead.server";
import type { UnitsState } from "~/lib/showing-units";
import { loader as homeLoader } from "~/routes/_index";
import { loader as listLoader } from "~/routes/_sernia-public.showings._index";
import { loader as unitLoader } from "~/routes/_sernia-public.showings.$unit";

// The units list and the booking pages are read before they are opened: the
// home page starts the list, the list starts its booking pages. Only a
// navigation inside the site takes what was read.

for (const name of ["SERNIA_PUBLIC_HOSTS", "SERNIA_PUBLIC_PAGES_ENABLED", "RAILWAY_ENVIRONMENT_NAME"]) {
  delete process.env[name];
}

const SITE = "https://serniacapital.com";
const unit = (name: string) => ({
  unit: name,
  address: "320 Example St",
  rent: 1450,
  lease_start_earliest: null,
  lease_start_latest: null,
  lease_start: null,
  has_open_times: true,
});

type Answer = (path: string) => Promise<Response> | Response;

/** Runs `run` with the API answered by `answer`; `calls` lists the paths it was asked. */
async function withApi(answer: Answer, run: (calls: string[]) => Promise<void>) {
  const calls: string[] = [];
  const realFetch = globalThis.fetch;
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    const path = new URL(String(input)).pathname.replace("/api/showing-bookings/public", "");
    calls.push(path);
    return answer(path);
  }) as typeof fetch;
  try {
    await run(calls);
  } finally {
    globalThis.fetch = realFetch;
    forgetReadAhead();
  }
}

const open =
  (units: string[]): Answer =>
  (path) => {
    if (path === "/call-line") return Response.json({ turnstile_site_key: null });
    if (path === "/units") return Response.json({ units: units.map(unit), turnstile_site_key: null });
    return Response.json({ unit: path.split("/").at(-1), offers: [] });
  };

const request = (path: string, headers: HeadersInit = {}) => new Request(`${SITE}${path}`, { headers });
const home = (headers?: HeadersInit) => homeLoader({ url: new URL(`${SITE}/`), request: request("/", headers) } as never);
/** The list page's data. Its list may still be a promise, so it stays inside the object. */
const openList = (path: string, headers?: HeadersInit) => listLoader({ request: request(path, headers) } as never);
const readList = async (path: string, headers?: HeadersInit) => await (await openList(path, headers)).list;
const page = (name: string, path = `/showings/${name}.data`) =>
  unitLoader({ params: { unit: name }, request: request(path) } as never);
const settled = () => new Promise((resolve) => setTimeout(resolve, 5));

test("from home to the list to a booking page, each is read once and ahead", async () => {
  await withApi(open(["320-03", "320-05"]), async (calls) => {
    await home();
    await settled();
    const { list: shown } = await openList("/showings.data");
    // Already read, so it is in the page with no placeholder before it.
    assert.ok(!(shown instanceof Promise));
    assert.equal((shown as UnitsState).status, "open");
    await settled();
    assert.deepEqual(calls, ["/call-line", "/units", "/units/320-03", "/units/320-05"]);

    assert.equal((await page("320-05")).unit, "320-05");
    assert.equal(calls.length, 4);
  });
});

test("the list page opens before its list is read", async () => {
  let answer: (response: Response) => void = () => {};
  const slow = new Promise<Response>((resolve) => (answer = resolve));
  await withApi(
    (path) => (path === "/units" ? slow : open([])(path)),
    async () => {
      const { list: shown } = await openList("/showings");
      assert.ok(shown instanceof Promise);
      answer(Response.json({ units: [], turnstile_site_key: null }));
      assert.deepEqual(await shown, { status: "open", units: [], turnstileSiteKey: null });
    },
  );
});

test("a page load reads the API, whatever was read ahead", async () => {
  await withApi(open(["320-03"]), async (calls) => {
    await home();
    await settled();
    await readList("/showings");
    await settled();
    await page("320-03", "/showings/320-03");
    assert.deepEqual(calls, ["/call-line", "/units", "/units", "/units/320-03", "/units/320-03"]);
  });
});

test("a read ahead that found nothing open is read again", async () => {
  let booking = false;
  await withApi(
    (path) => (path === "/units" && !booking ? Response.json(null, { status: 404 }) : open(["320-03"])(path)),
    async () => {
      await home();
      await settled();
      booking = true;
      assert.equal((await readList("/showings.data")).status, "open");
    },
  );
  let unitOpen = false;
  await withApi(
    (path) => (path === "/units/320-03" && !unitOpen ? Response.json(null, { status: 404 }) : open(["320-03"])(path)),
    async () => {
      await readList("/showings");
      await settled();
      unitOpen = true;
      assert.equal((await page("320-03")).unit, "320-03");
    },
  );
});

test("a long list reads only its first booking pages ahead, and a bot's visit none", async () => {
  const names = Array.from({ length: 9 }, (_, n) => `320-0${n + 1}`);
  await withApi(open(names), async (calls) => {
    await readList("/showings", { "user-agent": "Googlebot/2.1" });
    await settled();
    assert.deepEqual(calls, ["/units"]);
    await readList("/showings");
    await settled();
    assert.equal(calls.length, 2 + 6);
  });
});

test("an answer is kept for a minute, and only for navigations inside the site", async (t) => {
  t.mock.timers.enable({ apis: ["Date"], now: 1_000_000 });
  const inside = request("/showings.data");
  readAhead("list", async () => "first");
  assert.equal(await takeReadAhead("list", inside), "first");
  assert.equal(takeReadAhead("list", request("/showings")), null);
  assert.equal(takeReadAhead("other", inside), null);
  t.mock.timers.setTime(1_060_000);
  assert.equal(await takeReadAhead("list", inside), "first");
  t.mock.timers.setTime(1_060_001);
  assert.equal(takeReadAhead("list", inside), null);
  forgetReadAhead();
});
