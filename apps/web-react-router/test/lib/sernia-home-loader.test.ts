import assert from "node:assert/strict";
import test from "node:test";

import { readCallLine } from "~/lib/showing-units.server";
import { loader } from "~/routes/_index";

// The Sernia home page's loader reads only the call line's route, and renders
// without the call line when that route is off, slow or failing.

for (const name of ["SERNIA_PUBLIC_HOSTS", "SERNIA_PUBLIC_PAGES_ENABLED", "RAILWAY_ENVIRONMENT_NAME"]) {
  delete process.env[name];
}

type Answer = (url: string, init?: RequestInit) => Promise<Response>;

async function withFetch(answer: Answer, run: (calls: string[]) => Promise<void>) {
  const calls: string[] = [];
  const realFetch = globalThis.fetch;
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push(String(input));
    return answer(String(input), init);
  }) as typeof fetch;
  try {
    await run(calls);
  } finally {
    globalThis.fetch = realFetch;
  }
}

const home = (origin = "https://serniacapital.com") => loader({ url: new URL(`${origin}/`) } as never);

test("with the call line's route open, the home page shows the call line, and reads nothing else", async () => {
  await withFetch(
    async () => Response.json({ turnstile_site_key: "1x00000000000000000000AA" }),
    async (calls) => {
      assert.deepEqual(await home(), { serniaHome: { bookingOpen: true, turnstileSiteKey: "1x00000000000000000000AA" } });
      assert.equal(calls.length, 1);
      assert.match(calls[0], /\/api\/showing-bookings\/public\/call-line$/);
      assert.ok(!calls.some((url) => url.includes("/public/units")));
    },
  );
});

test("with public booking off (404), the home page renders without the call line", async () => {
  await withFetch(
    async () => Response.json({ detail: "Not Found" }, { status: 404 }),
    async () => {
      assert.deepEqual(await home(), { serniaHome: { bookingOpen: false, turnstileSiteKey: null } });
    },
  );
});

test("a failing call line route leaves the call line out", async () => {
  for (const answer of [
    async () => new Response("Internal Server Error", { status: 500 }),
    async () => new Response("not json", { status: 200 }),
    async () => {
      throw new TypeError("fetch failed");
    },
  ] satisfies Answer[]) {
    await withFetch(answer, async () => {
      assert.deepEqual(await home(), { serniaHome: { bookingOpen: false, turnstileSiteKey: null } });
    });
  }
});

test("a slow call line route is given up on after the timeout", async () => {
  // Answers only when the request is aborted, as a hung API would.
  const hung: Answer = (_url, init) =>
    new Promise((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => reject(init.signal?.reason));
    });
  // AbortSignal.timeout's timer does not hold the event loop open; this does, until the answer.
  const keepAlive = setInterval(() => {}, 1_000);
  try {
    await withFetch(hung, async () => {
      const started = Date.now();
      assert.deepEqual(await readCallLine(50), { bookingOpen: false, turnstileSiteKey: null });
      assert.ok(Date.now() - started < 1_000);
    });
  } finally {
    clearInterval(keepAlive);
  }
});

test("on other hosts the loader makes no request", async () => {
  await withFetch(
    async () => {
      throw new Error("no request expected");
    },
    async (calls) => {
      assert.deepEqual(await home("http://localhost:5293"), { serniaHome: null });
      assert.deepEqual(await home("https://eesposito.com"), { serniaHome: null });
      assert.deepEqual(calls, []);
    },
  );
});
