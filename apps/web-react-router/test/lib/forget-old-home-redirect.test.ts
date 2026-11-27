import assert from "node:assert/strict";
import test from "node:test";
import {
  CLEAR_SITE_CACHE_PATH,
  clearSiteCacheResponse,
  clearSiteCacheUrls,
  forgetOldHomeRedirect,
  SITE_CACHE_CLEARED_KEY,
} from "../../app/sites/team/lib/forget-old-home-redirect.ts";
import { routeTable } from "./route-table.ts";

const TWIN = `//www.serniacapital.com${CLEAR_SITE_CACHE_PATH}`;

function memoryStore(initial: Record<string, string> = {}) {
  const values = new Map(Object.entries(initial));
  return {
    values,
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => void values.set(key, value),
  };
}

function recordingFetch(answer: (input: string) => { ok: boolean } | Error = () => ({ ok: true })) {
  const calls: { input: string; init: RequestInit }[] = [];
  const fetcher = async (input: string, init: RequestInit) => {
    calls.push({ input, init });
    const result = answer(input);
    if (result instanceof Error) throw result;
    return result;
  };
  return { calls, fetcher };
}

test("the clearing request is a team route", () => {
  assert.ok(routeTable().some((route) => route.path === CLEAR_SITE_CACHE_PATH));
});

test("the clearing answer clears only the cache and is never stored", () => {
  const response = clearSiteCacheResponse();
  assert.equal(response.status, 204);
  assert.equal(response.headers.get("Clear-Site-Data"), '"cache"');
  assert.equal(response.headers.get("Cache-Control"), "no-store");
});

test("a browser asks its own host and the www twin, where the public site is at /", () => {
  assert.deepEqual(clearSiteCacheUrls(new URL("https://serniacapital.com/team/chat"), true), [CLEAR_SITE_CACHE_PATH, TWIN]);
  // The local Sernia host has no twin; the port stays on one that does.
  assert.deepEqual(clearSiteCacheUrls(new URL("http://serniacapital.localhost:5293/team"), true), [CLEAR_SITE_CACHE_PATH]);
  assert.deepEqual(clearSiteCacheUrls(new URL("https://serniacapital.com:8443/team"), true), [
    CLEAR_SITE_CACHE_PATH,
    `//www.serniacapital.com:8443${CLEAR_SITE_CACHE_PATH}`,
  ]);
  assert.deepEqual(clearSiteCacheUrls(new URL("https://serniacapital.com/team"), false), []);
});

test("asks both hosts, the twin with credentials, and remembers the clearing finished", async () => {
  const store = memoryStore();
  const { calls, fetcher } = recordingFetch();

  assert.equal(await forgetOldHomeRedirect([CLEAR_SITE_CACHE_PATH, TWIN], fetcher, store), true);

  assert.deepEqual(calls, [
    { input: CLEAR_SITE_CACHE_PATH, init: { cache: "no-store", credentials: "same-origin" } },
    // Chrome skips the clearing for another host's answer unless the request carried credentials.
    { input: TWIN, init: { mode: "no-cors", cache: "no-store", credentials: "include" } },
  ]);
  assert.match(store.values.get(SITE_CACHE_CLEARED_KEY) ?? "", /^\d{4}-\d\d-\d\dT/);
});

test("does nothing once the browser has seen the clearing finish", async () => {
  const store = memoryStore({ [SITE_CACHE_CLEARED_KEY]: "2026-11-27T00:00:00.000Z" });
  const { calls, fetcher } = recordingFetch();

  assert.equal(await forgetOldHomeRedirect([CLEAR_SITE_CACHE_PATH], fetcher, store), false);
  assert.equal(calls.length, 0);
});

test("counts a try before asking, so a closed tab does not start over on every visit", async () => {
  const store = memoryStore();
  const seen: (string | undefined)[] = [];
  const fetcher = async () => {
    // What the store holds while the request is out, as it would if the tab closed now.
    seen.push(store.values.get(SITE_CACHE_CLEARED_KEY));
    return { ok: false };
  };

  for (let visit = 0; visit < 5; visit += 1) await forgetOldHomeRedirect([CLEAR_SITE_CACHE_PATH], fetcher, store);

  assert.deepEqual(seen, ["1", "2", "3"]);
  assert.equal(store.values.get(SITE_CACHE_CLEARED_KEY), "3");
});

test("a failed twin leaves the clearing unfinished, to try again", async () => {
  const store = memoryStore();
  const { calls, fetcher } = recordingFetch((input) => (input === TWIN ? new Error("offline") : { ok: true }));

  await assert.rejects(forgetOldHomeRedirect([CLEAR_SITE_CACHE_PATH, TWIN], fetcher, store));
  assert.equal(calls.length, 2);
  assert.equal(store.values.get(SITE_CACHE_CLEARED_KEY), "1");
});

test("skips where there is nothing to ask, no fetch or no storage, as on the server", async () => {
  const { calls, fetcher } = recordingFetch();
  assert.equal(await forgetOldHomeRedirect([], fetcher, memoryStore()), false);
  assert.equal(await forgetOldHomeRedirect([CLEAR_SITE_CACHE_PATH], null, memoryStore()), false);
  assert.equal(await forgetOldHomeRedirect([CLEAR_SITE_CACHE_PATH], fetcher, null), false);
  assert.equal(calls.length, 0);
});
