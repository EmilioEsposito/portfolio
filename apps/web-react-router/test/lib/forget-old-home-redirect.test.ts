import assert from "node:assert/strict";
import test from "node:test";
import {
  CLEAR_SITE_CACHE_PATH,
  clearSiteCacheResponse,
  forgetOldHomeRedirect,
  SITE_CACHE_CLEARED_KEY,
} from "../../app/sites/team/lib/forget-old-home-redirect.ts";
import { routeTable } from "./route-table.ts";

function memoryStore(initial: Record<string, string> = {}) {
  const values = new Map(Object.entries(initial));
  return {
    values,
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => void values.set(key, value),
  };
}

function recordingFetch(ok: boolean) {
  const calls: { input: string; init: RequestInit }[] = [];
  const fetcher = async (input: string, init: RequestInit) => {
    calls.push({ input, init });
    return { ok };
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

test("asks for the clearing answer and remembers it did", async () => {
  const store = memoryStore();
  const { calls, fetcher } = recordingFetch(true);

  assert.equal(await forgetOldHomeRedirect(fetcher, store), true);

  assert.deepEqual(calls, [{ input: CLEAR_SITE_CACHE_PATH, init: { cache: "no-store", credentials: "same-origin" } }]);
  assert.ok(store.values.get(SITE_CACHE_CLEARED_KEY));
});

test("does nothing once the browser has done it", async () => {
  const store = memoryStore({ [SITE_CACHE_CLEARED_KEY]: "2026-11-27T00:00:00.000Z" });
  const { calls, fetcher } = recordingFetch(true);

  assert.equal(await forgetOldHomeRedirect(fetcher, store), false);
  assert.equal(calls.length, 0);
});

test("tries again on the next page load when the server fails", async () => {
  const store = memoryStore();
  const { calls, fetcher } = recordingFetch(false);

  assert.equal(await forgetOldHomeRedirect(fetcher, store), true);
  assert.equal(calls.length, 1);
  assert.equal(store.values.has(SITE_CACHE_CLEARED_KEY), false);
});

test("skips where there is no fetch or storage, as on the server", async () => {
  const { fetcher } = recordingFetch(true);
  assert.equal(await forgetOldHomeRedirect(null, memoryStore()), false);
  assert.equal(await forgetOldHomeRedirect(fetcher, null), false);
});
