import assert from "node:assert/strict";
import test from "node:test";

import { loader } from "~/routes/_sernia-public.showings.$unit";

// The booking page's loader: a unit that is not open is a 404, except for a lead
// with a place there, whose own link shows it whatever the unit answers others.

const UNIT = { unit: "320-03", address: "320 Example St", offers: [], confirmations: [] };
const PLACE = { unit: "320-03", address: "320 Example St", details_added: false, texted: false };
const TOKEN = "lead-token-0123456789";

type Answers = { unit?: number | "down"; place?: number; placeUnit?: number };

async function load(answers: Answers, query = "") {
  const calls: { url: string; body: string | null }[] = [];
  const realFetch = globalThis.fetch;
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    calls.push({ url, body: init?.body ? String(init.body) : null });
    const answer = (status: number, body: unknown) => Response.json(status === 200 ? body : null, { status });
    if (url.endsWith("/lead-booking/unit")) return answer(answers.placeUnit ?? 404, UNIT);
    if (url.endsWith("/lead-booking")) return answer(answers.place ?? 404, PLACE);
    if (answers.unit === "down") throw new TypeError("fetch failed");
    return answer(answers.unit ?? 200, UNIT);
  }) as typeof fetch;
  try {
    const request = new Request(`https://serniacapital.com/showings/320-03${query}`);
    const result = await loader({ params: { unit: "320-03" }, request } as never).then(
      (body) => ({ status: 200, body }),
      (thrown) => ({ status: thrown.init?.status as number, body: null }),
    );
    return { ...result, calls };
  } finally {
    globalThis.fetch = realFetch;
  }
}

test("an open unit shows the booking form, or the lead's own place", async () => {
  const open = await load({});
  assert.deepEqual([open.status, open.body?.lead_booking, open.calls.length], [200, null, 1]);
  const own = await load({ place: 200 }, `?t=${TOKEN}`);
  assert.deepEqual([own.status, own.body?.unit, own.body?.lead_booking], [200, "320-03", PLACE]);
  assert.ok(!own.calls.some((call) => call.url.endsWith("/lead-booking/unit")));
});

test("a unit that is not open is a 404, with any link that has no place there", async () => {
  assert.equal((await load({ unit: 404 })).status, 404);
  assert.equal((await load({ unit: 404 }, `?t=${TOKEN}`)).status, 404);
  assert.equal((await load({ unit: 404, place: 200 }, "?t=short")).status, 404);
  // A place that is gone, or whose unit cannot be read, shows nothing either.
  assert.equal((await load({ unit: 404, place: 200, placeUnit: 404 }, `?t=${TOKEN}`)).status, 404);
});

test("a lead with a place at a unit that is not open sees it, and the token stays in the body", async () => {
  const own = await load({ unit: 404, place: 200, placeUnit: 200 }, `?t=${TOKEN}`);
  assert.deepEqual([own.status, own.body?.address, own.body?.lead_booking], [200, "320 Example St", PLACE]);
  assert.ok(own.calls.every((call) => !call.url.includes(TOKEN)));
  assert.equal(own.calls.filter((call) => call.body?.includes(TOKEN)).length, 2);
});

test("an API that does not answer is a 503, never a 404", async () => {
  assert.equal((await load({ unit: "down" })).status, 503);
  assert.equal((await load({ unit: 500, place: 200, placeUnit: 200 }, `?t=${TOKEN}`)).status, 503);
});
