import assert from "node:assert/strict";
import test from "node:test";
import { data, UNSAFE_ErrorResponseImpl as ErrorResponseImpl } from "react-router";

import { createRootMiddleware, loadRoot } from "~/sites/root-request.server";
import { serniaRoot } from "~/sites/sernia-root";
import { serniaHomeMeta } from "~/sites/sernia-public/components/home";

// What the server says about a Sernia page, as the browser reads it: whether
// it is one, and whether `/` on its host is the Sernia home page.

const ENVIRONMENT = ["SERNIA_PUBLIC_HOSTS", "SERNIA_PUBLIC_PAGES_ENABLED", "RAILWAY_ENVIRONMENT_NAME"] as const;
for (const name of ENVIRONMENT) delete process.env[name];

const rootData = (url: string) => loadRoot({ url: new URL(url) }, () => ({ clerkState: "stand-in" }));

/** The error a page gets when the root middleware throws. */
function thrownBy(url: string): unknown {
  const middleware = createRootMiddleware((_args, next) => next());
  try {
    void middleware({ url: new URL(url) } as never, async () => new Response());
  } catch (thrown) {
    const { data: body, init } = thrown as ReturnType<typeof data>;
    return new ErrorResponseImpl(init?.status ?? 0, init?.statusText, body);
  }
  return undefined;
}

test("a Sernia page knows whether / on its host is the Sernia home page", () => {
  for (const origin of ["https://serniacapital.com", "https://www.serniacapital.com", "http://serniacapital.localhost:5293"]) {
    for (const path of ["/", "/showings", "/privacy"]) {
      assert.deepEqual(serniaRoot(undefined, rootData(origin + path)), { homeAtRoot: true }, origin + path);
    }
  }
  for (const origin of ["https://eesposito.com", "http://localhost:5293", "https://react-router-portfolio-pr-1.up.railway.app"]) {
    assert.deepEqual(serniaRoot(undefined, rootData(`${origin}/showings`)), { homeAtRoot: false }, origin);
  }
});

test("so does the Sernia 404, and while the pages are off nothing is the home page", () => {
  assert.deepEqual(serniaRoot(thrownBy("https://serniacapital.com/nothing"), undefined), { homeAtRoot: true });
  process.env.SERNIA_PUBLIC_PAGES_ENABLED = "false";
  try {
    assert.deepEqual(serniaRoot(thrownBy("https://serniacapital.com/"), undefined), { homeAtRoot: false });
  } finally {
    delete process.env.SERNIA_PUBLIC_PAGES_ENABLED;
  }
});

test("every other page is not a Sernia page", () => {
  assert.equal(serniaRoot(undefined, rootData("https://eesposito.com/")), null);
  assert.equal(serniaRoot(new ErrorResponseImpl(404, "Not Found", null), undefined), null);
  assert.equal(serniaRoot(new Error("boom"), undefined), null);
  assert.equal(serniaRoot(undefined, undefined), null);
});

test("the home page's meta names the page, its image and its canonical address", () => {
  const meta = serniaHomeMeta() as Record<string, string | undefined>[];
  const find = (key: string, value: string) => meta.find((item) => item[key] === value);
  assert.equal(meta.find((item) => "title" in item)?.title, "Sernia Capital | Apartments in Bloomfield and Shadyside");
  assert.equal(find("property", "og:image")?.content, "https://serniacapital.com/images/sernia/og.png");
  assert.equal(find("rel", "canonical")?.href, "https://serniacapital.com/");
  assert.ok(find("name", "description")?.content);
});
