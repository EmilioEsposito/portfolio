import assert from "node:assert/strict";
import test from "node:test";
import {
  forgetOldHomeRedirect,
  OLD_HOME_REDIRECT_FORGOTTEN_KEY,
} from "../../app/sites/team/lib/forget-old-home-redirect.ts";

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

test("asks for the home page past the cache and remembers it did", async () => {
  const store = memoryStore();
  const { calls, fetcher } = recordingFetch(true);

  assert.equal(await forgetOldHomeRedirect(fetcher, store), true);

  assert.deepEqual(calls, [{ input: "/", init: { cache: "reload", credentials: "same-origin" } }]);
  assert.ok(store.values.get(OLD_HOME_REDIRECT_FORGOTTEN_KEY));
});

test("does nothing once the browser has done it", async () => {
  const store = memoryStore({ [OLD_HOME_REDIRECT_FORGOTTEN_KEY]: "2026-11-27T00:00:00.000Z" });
  const { calls, fetcher } = recordingFetch(true);

  assert.equal(await forgetOldHomeRedirect(fetcher, store), false);
  assert.equal(calls.length, 0);
});

test("tries again on the next page load when the server fails", async () => {
  const store = memoryStore();
  const { calls, fetcher } = recordingFetch(false);

  assert.equal(await forgetOldHomeRedirect(fetcher, store), true);
  assert.equal(calls.length, 1);
  assert.equal(store.values.has(OLD_HOME_REDIRECT_FORGOTTEN_KEY), false);
});

test("skips where there is no fetch or storage, as on the server", async () => {
  const { fetcher } = recordingFetch(true);
  assert.equal(await forgetOldHomeRedirect(null, memoryStore()), false);
  assert.equal(await forgetOldHomeRedirect(fetcher, null), false);
});
