import assert from "node:assert/strict";
import test from "node:test";

import {
  DEFAULT_SERNIA_PUBLIC_HOSTS,
  isApiProxyAllowed,
  isHomePath,
  isSerniaHomeRequest,
  isSerniaHost,
  isSerniaPublicPath,
  rootRequestMode,
  serniaPublicHosts,
  serniaPublicPagesEnabled,
} from "~/sites/site";

import { routeTable, sampleUrl } from "./route-table";

// Outside production, with no overrides: the pages are on. Each test that
// needs another environment sets it and restores this state.
const ENVIRONMENT = ["SERNIA_PUBLIC_HOSTS", "SERNIA_PUBLIC_PAGES_ENABLED", "RAILWAY_ENVIRONMENT_NAME"] as const;
for (const name of ENVIRONMENT) delete process.env[name];

function withEnvironment(values: Partial<Record<(typeof ENVIRONMENT)[number], string>>, run: () => void) {
  Object.assign(process.env, values);
  try {
    run();
  } finally {
    for (const name of ENVIRONMENT) delete process.env[name];
  }
}

test("recognizes the Sernia hosts, with www, a port, upper case or a trailing dot", () => {
  for (const host of [
    "serniacapital.com",
    "www.serniacapital.com",
    "serniacapital.localhost",
    "serniacapital.localhost:5293",
    "serniacapital.com:443",
    "SerniaCapital.COM",
    "WWW.SERNIACAPITAL.COM:8443",
    "serniacapital.com.",
  ]) {
    assert.equal(isSerniaHost(host), true, host);
  }
  for (const host of [
    "eesposito.com",
    "localhost:5293",
    "127.0.0.1:5293",
    "[::1]:5293",
    "react-router-portfolio-pr-123.up.railway.app",
    "api.serniacapital.com",
    "notserniacapital.com",
    "serniacapital.com.example.test",
    "",
    undefined,
    null,
  ]) {
    assert.equal(isSerniaHost(host), false, String(host));
  }
});

test("SERNIA_PUBLIC_HOSTS replaces the default hosts", () => {
  withEnvironment({ SERNIA_PUBLIC_HOSTS: " Preview.Example.test:8080 , other.example.test,, " }, () => {
    assert.deepEqual(serniaPublicHosts(), ["preview.example.test", "other.example.test"]);
    assert.equal(isSerniaHost("preview.example.test:5173"), true);
    assert.equal(isSerniaHost("OTHER.example.test"), true);
    assert.equal(isSerniaHost("serniacapital.com"), false);
    assert.equal(rootRequestMode("https://preview.example.test/sernia-capital"), "sernia-not-found");
    assert.equal(rootRequestMode("https://preview.example.test/"), "sernia-public");
    assert.equal(rootRequestMode("https://serniacapital.com/"), "portfolio");
    assert.equal(isApiProxyAllowed("preview.example.test", "/api/ai-demos/chat-emilio"), false);
    assert.equal(isApiProxyAllowed("serniacapital.com", "/api/ai-demos/chat-emilio"), true);
  });
  assert.equal(isSerniaHost("serniacapital.com"), true);
});

test("the pages are off in production unless SERNIA_PUBLIC_PAGES_ENABLED is true", () => {
  for (const [setting, enabled] of [
    [undefined, false],
    ["", false],
    ["true", true],
    [" TRUE ", true],
    ["1", false],
    ["yes", false],
    ["false", false],
  ] as const) {
    assert.equal(serniaPublicPagesEnabled(setting, "production"), enabled, `production, ${String(setting)}`);
    assert.equal(serniaPublicPagesEnabled(setting, " Production "), enabled, `Production, ${String(setting)}`);
  }
});

test("outside production the pages are on unless SERNIA_PUBLIC_PAGES_ENABLED is false", () => {
  for (const environment of [undefined, "", "development", "portfolio-pr-397"]) {
    for (const [setting, enabled] of [
      [undefined, true],
      ["", true],
      ["true", true],
      ["false", false],
      [" FALSE ", false],
      ["0", true],
    ] as const) {
      assert.equal(serniaPublicPagesEnabled(setting, environment), enabled, `${String(environment)}, ${String(setting)}`);
    }
  }
});

test("the switch is read from the environment on each call", () => {
  assert.equal(serniaPublicPagesEnabled(), true);
  withEnvironment({ RAILWAY_ENVIRONMENT_NAME: "production" }, () => {
    assert.equal(serniaPublicPagesEnabled(), false);
    assert.equal(rootRequestMode("https://eesposito.com/privacy"), "portfolio");
    assert.equal(rootRequestMode("https://serniacapital.com/privacy"), "sernia-not-found");
  });
  withEnvironment({ RAILWAY_ENVIRONMENT_NAME: "production", SERNIA_PUBLIC_PAGES_ENABLED: "true" }, () => {
    assert.equal(serniaPublicPagesEnabled(), true);
    assert.equal(rootRequestMode("https://eesposito.com/privacy"), "sernia-public");
    assert.equal(rootRequestMode("https://serniacapital.com/privacy"), "sernia-public");
  });
  withEnvironment({ RAILWAY_ENVIRONMENT_NAME: "portfolio-pr-397", SERNIA_PUBLIC_PAGES_ENABLED: "false" }, () => {
    assert.equal(serniaPublicPagesEnabled(), false);
    assert.equal(rootRequestMode("https://react-router-portfolio-pr-397.up.railway.app/privacy"), "portfolio");
  });
});

test("while the pages are off, their paths are the portfolio's and a Sernia host answers 404 everywhere but /team", () => {
  const off = { pagesEnabled: false };
  for (const path of ["/privacy", "/sms-terms", "/showings", "/showings/320-03", "/", "/sernia-capital"]) {
    assert.equal(rootRequestMode(`https://serniacapital.com${path}`, off), "sernia-not-found", path);
    assert.equal(rootRequestMode(`https://www.serniacapital.com${path}`, off), "sernia-not-found", path);
    assert.equal(rootRequestMode(`https://eesposito.com${path}`, off), "portfolio", path);
    assert.equal(rootRequestMode(`http://localhost:5293${path}`, off), "portfolio", path);
  }
  assert.equal(rootRequestMode("https://serniacapital.com/team", off), "team");
  assert.equal(rootRequestMode("https://eesposito.com/privacy", { pagesEnabled: true }), "sernia-public");
});

test("the /api rule does not depend on the pages switch", () => {
  withEnvironment({ RAILWAY_ENVIRONMENT_NAME: "production" }, () => {
    assert.equal(serniaPublicPagesEnabled(), false);
    assert.equal(isApiProxyAllowed("serniacapital.com", "/api/showing-bookings/public/units"), true);
    assert.equal(isApiProxyAllowed("serniacapital.com", "/api/sernia-ai/chat"), true);
    assert.equal(isApiProxyAllowed("serniacapital.com", "/api/ai-demos/chat-emilio"), false);
    assert.equal(isApiProxyAllowed("eesposito.com", "/api/ai-demos/chat-emilio"), true);
  });
});

test("a blank SERNIA_PUBLIC_HOSTS keeps the default hosts", () => {
  for (const setting of ["", " ", ", ,"]) {
    assert.deepEqual(serniaPublicHosts(setting), [...DEFAULT_SERNIA_PUBLIC_HOSTS]);
  }
  assert.equal(isSerniaHost("www.serniacapital.com", ""), true);
});

test("Sernia public paths are /showings, /showings/*, /privacy and /sms-terms", () => {
  for (const path of [
    "/showings",
    "/showings/",
    "/showings/320-03",
    "/showings/cancel/abc123",
    "/privacy",
    "/privacy/",
    "/sms-terms",
    // React Router ignores case and decodes the path before matching routes.
    "/PRIVACY",
    "/Showings/320-03",
    "/priv%61cy",
  ]) {
    assert.equal(isSerniaPublicPath(path), true, path);
  }
  for (const path of [
    "/",
    "/showingsx",
    "/showings-list",
    "/privacy-policy",
    "/privacy/extra",
    "/sms-terms/extra",
    "/sms",
    "/sernia-capital",
    "/team",
    "/team/leads/bookings",
    "/api/showing-bookings/public/units",
    "/showings%2Fx",
    "/privacy%",
  ]) {
    assert.equal(isSerniaPublicPath(path), false, path);
  }
});

test("a Sernia public page is any path on a Sernia host it serves, or a Sernia public path on any host", () => {
  for (const url of [
    "https://serniacapital.com/privacy",
    "https://www.serniacapital.com/sms-terms",
    "https://serniacapital.com/",
    "http://serniacapital.localhost:5293/privacy",
    "http://localhost:5293/privacy",
    "https://react-router-portfolio-pr-123.up.railway.app/sms-terms",
    "https://eesposito.com/showings/320-03",
    "https://SERNIACAPITAL.COM:443/privacy",
  ]) {
    assert.equal(rootRequestMode(url), "sernia-public", url);
  }
  for (const url of [
    "https://eesposito.com/",
    "https://eesposito.com/sernia-capital",
    "http://localhost:5293/systems/leasing-leads",
  ]) {
    assert.equal(rootRequestMode(url), "portfolio", url);
  }
});

test("on a Sernia host every path but the public ones, the home page, robots.txt and /team answers 404", () => {
  for (const path of [
    "/sernia-capital",
    "/systems/leasing-leads",
    "/sernia-chat",
    "/sitemap.xml",
    "/index",
    "/apply",
    "/_index",
    "/teams",
  ]) {
    assert.equal(rootRequestMode(`https://serniacapital.com${path}`), "sernia-not-found", path);
    assert.equal(rootRequestMode(`http://serniacapital.localhost:5293${path}`), "sernia-not-found", path);
  }
  for (const path of ["/", "/privacy", "/sms-terms", "/showings", "/showings/320-03", "/robots.txt"]) {
    assert.equal(rootRequestMode(`https://www.serniacapital.com${path}`), "sernia-public", path);
  }
  // Other hosts keep every other page, and show the Sernia pages at the same paths.
  assert.equal(rootRequestMode("https://eesposito.com/"), "portfolio");
  assert.equal(rootRequestMode("http://localhost:5293/robots.txt"), "portfolio");
  assert.equal(rootRequestMode("http://localhost:5293/privacy"), "sernia-public");
});

test("/ is the Sernia home page on a Sernia host while the pages are on, and the personal site's elsewhere", () => {
  const on = { pagesEnabled: true };
  const off = { pagesEnabled: false };
  for (const url of [
    "https://serniacapital.com/",
    "https://serniacapital.com",
    "https://www.serniacapital.com/?ref=card",
    "https://SERNIACAPITAL.COM:443/",
    "http://serniacapital.localhost:5293/",
  ]) {
    assert.equal(rootRequestMode(url, on), "sernia-public", url);
    assert.equal(isSerniaHomeRequest(url, on), true, url);
    assert.equal(rootRequestMode(url, off), "sernia-not-found", url);
    assert.equal(isSerniaHomeRequest(url, off), false, url);
  }
  for (const url of [
    "https://eesposito.com/",
    "http://localhost:5293/",
    "http://127.0.0.1:5293/",
    "https://react-router-portfolio-pr-123.up.railway.app/",
    "https://api.serniacapital.com/",
  ]) {
    for (const options of [on, off]) {
      assert.equal(rootRequestMode(url, options), "portfolio", url);
      assert.equal(isSerniaHomeRequest(url, options), false, url);
    }
  }
  // The home page is the only path the host adds; other Sernia pages are the same on any host.
  assert.equal(isSerniaHomeRequest("https://serniacapital.com/privacy", on), false);
  assert.equal(isHomePath("/"), true);
  assert.equal(isHomePath(""), true);
  assert.equal(isHomePath("/showings"), false);
});

test("in production / on a Sernia host follows SERNIA_PUBLIC_PAGES_ENABLED", () => {
  withEnvironment({ RAILWAY_ENVIRONMENT_NAME: "production" }, () => {
    assert.equal(rootRequestMode("https://serniacapital.com/"), "sernia-not-found");
    assert.equal(rootRequestMode("https://eesposito.com/"), "portfolio");
  });
  withEnvironment({ RAILWAY_ENVIRONMENT_NAME: "production", SERNIA_PUBLIC_PAGES_ENABLED: "true" }, () => {
    assert.equal(rootRequestMode("https://serniacapital.com/"), "sernia-public");
    assert.equal(rootRequestMode("https://eesposito.com/"), "portfolio");
  });
});

// The routes as app/routes.ts declares them. The Sernia public pages are the
// ones under this layout.
const SERNIA_LAYOUT = "sites/sernia-public/routes/layout.tsx";
const routes = routeTable();

test("every page under the Sernia public layout is at a Sernia public path", () => {
  const pages = routes.filter((route) => route.parents.includes(SERNIA_LAYOUT));
  assert.ok(pages.length >= 2, "expected the privacy and SMS terms pages");
  for (const route of pages) {
    assert.equal(isSerniaPublicPath(sampleUrl(route.path)), true, `${route.file} is served at ${route.path}`);
  }
});

test("the home page is the only route at /, so on a Sernia host / can serve nothing else", () => {
  // The Sernia and team layouts are pathless, so they map to / too; they render no page of their own.
  const atHome = routes.filter(
    (route) => route.path === "/" && route.file !== SERNIA_LAYOUT && route.file !== "sites/team/routes/layout.tsx",
  );
  assert.deepEqual(atHome.map((route) => route.file), ["sites/home.tsx"]);
});

test("every page at a Sernia public path is under the Sernia public layout", () => {
  // The layout's middleware is what keeps a page off while the switch is off.
  for (const route of routes) {
    if (isSerniaPublicPath(sampleUrl(route.path))) {
      assert.ok(route.parents.includes(SERNIA_LAYOUT), `${route.file} must be under the Sernia public layout`);
    }
  }
});
