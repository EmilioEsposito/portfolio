import assert from "node:assert/strict";
import test from "node:test";
import { createRequestHandler, type MiddlewareFunction, type ServerBuild } from "react-router";

import { createRootMiddleware, loadRoot } from "~/lib/root-request.server";
import * as catchAllRoute from "~/routes/$";
import * as indexRoute from "~/routes/_index";
import * as serniaLayoutRoute from "~/routes/_sernia-public";
import * as privacyRoute from "~/routes/_sernia-public.privacy";

// Runs the root's real middleware and loader, the Sernia layout, the privacy
// page and the catch-all route through React Router's request handler: the
// server path of a page load, and of the .data request a client navigation
// makes. Clerk is a stand-in that marks the response; the tests have no keys.

const ENVIRONMENT = ["SERNIA_PUBLIC_HOSTS", "SERNIA_PUBLIC_PAGES_ENABLED", "RAILWAY_ENVIRONMENT_NAME"] as const;
for (const name of ENVIRONMENT) delete process.env[name];

async function withEnvironment(values: Partial<Record<(typeof ENVIRONMENT)[number], string>>, run: () => Promise<void>) {
  Object.assign(process.env, values);
  try {
    await run();
  } finally {
    for (const name of ENVIRONMENT) delete process.env[name];
  }
}

const clerkStandIn: MiddlewareFunction<Response> = async (_args, next) => {
  const response = await next();
  response.headers.set("x-test-clerk", "ran");
  return response;
};

const build = {
  entry: {
    module: {
      // A page load stops here; the tests need only its status and headers.
      default: async (
        _request: Request,
        status: number,
        headers: Headers,
        context: { serverHandoffStream: ReadableStream<Uint8Array> },
      ) => {
        await new Response(context.serverHandoffStream).text();
        return new Response("document", { status, headers });
      },
    },
  },
  routes: {
    root: {
      id: "root",
      path: "",
      module: {
        middleware: [createRootMiddleware(clerkStandIn)],
        loader: (args: { url: URL }) => loadRoot(args, () => ({ clerkState: "stand-in" })),
        default: () => null,
        ErrorBoundary: () => null,
      },
    },
    "routes/_sernia-public": { id: "routes/_sernia-public", parentId: "root", module: serniaLayoutRoute },
    "routes/_sernia-public.privacy": {
      id: "routes/_sernia-public.privacy",
      parentId: "routes/_sernia-public",
      path: "privacy",
      module: privacyRoute,
    },
    "routes/_index": { id: "routes/_index", parentId: "root", index: true, module: indexRoute },
    "routes/$": { id: "routes/$", parentId: "root", path: "*", module: catchAllRoute },
  },
  assets: {},
  basename: "/",
  publicPath: "/",
  assetsBuildDirectory: "build/client",
  future: {},
  ssr: true,
  isSpaMode: false,
  prerender: [],
  routeDiscovery: { mode: "lazy", manifestPath: "/__manifest" },
} as unknown as ServerBuild;

const handle = createRequestHandler(build, "production");

async function request(url: string, init?: RequestInit) {
  const response = await handle(new Request(url, init));
  return {
    status: response.status,
    clerk: response.headers.get("x-test-clerk") === "ran",
    body: await response.text(),
  };
}

// What the browser asks for when a link moves from another page to /privacy:
// the root revalidates and the Sernia layout's loader runs.
const PRIVACY_DATA = "/privacy.data?_routes=root,routes/_sernia-public";

test("outside production the pages render without Clerk, on any host", async () => {
  for (const origin of ["http://localhost:5293", "https://serniacapital.com"]) {
    const page = await request(`${origin}/privacy`);
    assert.deepEqual([page.status, page.clerk], [200, false], origin);
    const data = await request(`${origin}${PRIVACY_DATA}`);
    assert.deepEqual([data.status, data.clerk], [200, false], origin);
    assert.match(data.body, /serniaPublic/, origin);
  }
  const unknown = await request("http://localhost:5293/unknown");
  assert.deepEqual([unknown.status, unknown.clerk], [404, true]);
});

test("in production the pages stay off, and their paths answer like an unknown URL", async () => {
  await withEnvironment({ RAILWAY_ENVIRONMENT_NAME: "production" }, async () => {
    const unknown = await request("https://eesposito.com/unknown");
    assert.deepEqual([unknown.status, unknown.clerk], [404, true]);

    const page = await request("https://eesposito.com/privacy");
    assert.deepEqual([page.status, page.clerk], [404, true]);
    const post = await request("https://eesposito.com/privacy", { method: "POST" });
    const unknownPost = await request("https://eesposito.com/unknown", { method: "POST" });
    assert.equal(post.status, unknownPost.status);
    assert.equal(post.status, 404);

    // A client navigation's data request gets the same 404 and no Sernia data.
    const data = await request(`https://eesposito.com${PRIVACY_DATA}`);
    const unknownData = await request("https://eesposito.com/unknown.data?_routes=root,routes/$");
    assert.deepEqual([data.status, data.clerk], [404, true]);
    assert.deepEqual([unknownData.status, unknownData.clerk], [404, true]);
    assert.doesNotMatch(data.body, /serniaPublic|sernia-public-not-found/);
  });
});

test("in production a Sernia host answers the Sernia 404 for every path while the pages are off", async () => {
  await withEnvironment({ RAILWAY_ENVIRONMENT_NAME: "production" }, async () => {
    for (const path of ["/privacy", "/", "/unknown"]) {
      const page = await request(`https://serniacapital.com${path}`);
      assert.deepEqual([page.status, page.clerk], [404, false], path);
    }
    const data = await request(`https://serniacapital.com${PRIVACY_DATA}`);
    assert.deepEqual([data.status, data.clerk], [404, false]);
    assert.match(data.body, /sernia-public-not-found/);
  });
});

test("in production SERNIA_PUBLIC_PAGES_ENABLED=true serves the pages without Clerk", async () => {
  await withEnvironment({ RAILWAY_ENVIRONMENT_NAME: "production", SERNIA_PUBLIC_PAGES_ENABLED: "true" }, async () => {
    for (const origin of ["https://eesposito.com", "https://serniacapital.com"]) {
      const page = await request(`${origin}/privacy`);
      assert.deepEqual([page.status, page.clerk], [200, false], origin);
      const data = await request(`${origin}${PRIVACY_DATA}`);
      assert.deepEqual([data.status, data.clerk], [200, false], origin);
      assert.match(data.body, /serniaPublic/, origin);
    }
    const unknown = await request("https://serniacapital.com/unknown");
    assert.deepEqual([unknown.status, unknown.clerk], [404, false]);
  });
});

test("outside production SERNIA_PUBLIC_PAGES_ENABLED=false switches the pages off", async () => {
  await withEnvironment({ RAILWAY_ENVIRONMENT_NAME: "portfolio-pr-397", SERNIA_PUBLIC_PAGES_ENABLED: "false" }, async () => {
    const page = await request("https://react-router-portfolio-pr-397.up.railway.app/privacy");
    assert.deepEqual([page.status, page.clerk], [404, true]);
    const data = await request(`https://react-router-portfolio-pr-397.up.railway.app${PRIVACY_DATA}`);
    assert.deepEqual([data.status, data.clerk], [404, true]);
  });
});

// The index route's loader reads the call line's route for the Sernia home page.
// A stand-in answers the API: 200 with the site key, or 404 (public booking off).
async function withApi(status: 200 | 404, run: (calls: string[]) => Promise<void>) {
  const calls: string[] = [];
  const realFetch = globalThis.fetch;
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    calls.push(String(input));
    return status === 200
      ? Response.json({ turnstile_site_key: "1x00000000000000000000AA" })
      : Response.json({ detail: "Not Found" }, { status: 404 });
  }) as typeof fetch;
  try {
    await run(calls);
  } finally {
    globalThis.fetch = realFetch;
  }
}

// What the browser asks for when a link moves to /: React Router names the root's data `/_.data`.
const HOME_DATA = "/_.data?_routes=root,routes/_index";

test("on a Sernia host / is the Sernia home page, without Clerk", async () => {
  await withApi(200, async (calls) => {
    for (const origin of ["https://serniacapital.com", "http://serniacapital.localhost:5293"]) {
      const page = await request(`${origin}/`);
      assert.deepEqual([page.status, page.clerk], [200, false], origin);
      const data = await request(`${origin}${HOME_DATA}`);
      assert.deepEqual([data.status, data.clerk], [200, false], origin);
      assert.match(data.body, /serniaPublic/, origin);
      assert.match(data.body, /bookingOpen/, origin);
    }
    // Only the call line's route; never the units list, which reads calendars.
    assert.ok(calls.length > 0);
    assert.ok(calls.every((url) => url.endsWith("/api/showing-bookings/public/call-line")), calls.join());
  });
});

test("on other hosts / stays the personal site's home page, through Clerk, with no API call", async () => {
  await withApi(200, async (calls) => {
    for (const origin of ["http://localhost:5293", "https://eesposito.com"]) {
      const page = await request(`${origin}/`);
      assert.deepEqual([page.status, page.clerk], [200, true], origin);
      const data = await request(`${origin}${HOME_DATA}`);
      assert.deepEqual([data.status, data.clerk], [200, true], origin);
      assert.doesNotMatch(data.body, /serniaPublic|bookingOpen/, origin);
    }
    assert.deepEqual(calls, []);
  });
});

test("in production / on a Sernia host is the Sernia 404 until the pages are on", async () => {
  await withApi(404, async (calls) => {
    await withEnvironment({ RAILWAY_ENVIRONMENT_NAME: "production" }, async () => {
      const page = await request("https://serniacapital.com/");
      assert.deepEqual([page.status, page.clerk], [404, false]);
      const data = await request(`https://serniacapital.com${HOME_DATA}`);
      assert.deepEqual([data.status, data.clerk], [404, false]);
      assert.match(data.body, /sernia-public-not-found/);
      const personal = await request("https://eesposito.com/");
      assert.deepEqual([personal.status, personal.clerk], [200, true]);
    });
    assert.deepEqual(calls, []);
    await withEnvironment({ RAILWAY_ENVIRONMENT_NAME: "production", SERNIA_PUBLIC_PAGES_ENABLED: "true" }, async () => {
      // Public booking off: the page still renders, with no call line.
      const data = await request(`https://serniacapital.com${HOME_DATA}`);
      assert.deepEqual([data.status, data.clerk], [200, false]);
      assert.match(data.body, /bookingOpen/);
    });
  });
});
