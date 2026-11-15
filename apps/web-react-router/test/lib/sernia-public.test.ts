import assert from "node:assert/strict";
import { readdirSync } from "node:fs";
import test from "node:test";

import {
  DEFAULT_SERNIA_PUBLIC_HOSTS,
  isApiProxyAllowed,
  isSerniaHost,
  isSerniaPublicPath,
  isSerniaPublicRequest,
  rootRequestMode,
  serniaPublicHosts,
} from "~/lib/sernia-public";

// Each test that needs the override sets it and restores this state.
delete process.env.SERNIA_PUBLIC_HOSTS;

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
  process.env.SERNIA_PUBLIC_HOSTS = " Preview.Example.test:8080 , other.example.test,, ";
  try {
    assert.deepEqual(serniaPublicHosts(), ["preview.example.test", "other.example.test"]);
    assert.equal(isSerniaHost("preview.example.test:5173"), true);
    assert.equal(isSerniaHost("OTHER.example.test"), true);
    assert.equal(isSerniaHost("serniacapital.com"), false);
    assert.equal(rootRequestMode("https://preview.example.test/"), "sernia-not-found");
    assert.equal(rootRequestMode("https://serniacapital.com/"), "app");
    assert.equal(isApiProxyAllowed("preview.example.test", "/api/sernia-ai/chat"), false);
  } finally {
    delete process.env.SERNIA_PUBLIC_HOSTS;
  }
  assert.equal(isSerniaHost("serniacapital.com"), true);
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
    "/sernia-chat",
    "/systems/leasing-leads/bookings",
    "/api/showing-bookings/public/units",
    "/showings%2Fx",
    "/privacy%",
  ]) {
    assert.equal(isSerniaPublicPath(path), false, path);
  }
});

test("a Sernia public request is a Sernia host, or a Sernia public path on any host", () => {
  for (const url of [
    "https://serniacapital.com/privacy",
    "https://www.serniacapital.com/sms-terms",
    "https://serniacapital.com/",
    "http://serniacapital.localhost:5293/privacy",
    "http://localhost:5293/privacy",
    "https://react-router-portfolio-pr-123.up.railway.app/sms-terms",
    "https://eesposito.com/showings/320-03",
  ]) {
    assert.equal(isSerniaPublicRequest(url), true, url);
  }
  for (const url of [
    "https://eesposito.com/",
    "https://eesposito.com/sernia-capital",
    "http://localhost:5293/sernia-chat",
    "https://react-router-portfolio-pr-123.up.railway.app/systems/leasing-leads/bookings",
  ]) {
    assert.equal(isSerniaPublicRequest(url), false, url);
  }
  assert.equal(isSerniaPublicRequest(new URL("https://SERNIACAPITAL.COM:443/anything")), true);
});

test("on a Sernia host every path but the public ones answers 404", () => {
  for (const path of [
    "/",
    "/sernia-capital",
    "/systems/leasing-leads",
    "/sernia-chat",
    "/sitemap.xml",
    "/robots.txt",
  ]) {
    assert.equal(rootRequestMode(`https://serniacapital.com${path}`), "sernia-not-found", path);
    assert.equal(rootRequestMode(`http://serniacapital.localhost:5293${path}`), "sernia-not-found", path);
  }
  for (const path of ["/privacy", "/sms-terms", "/showings", "/showings/320-03"]) {
    assert.equal(rootRequestMode(`https://www.serniacapital.com${path}`), "sernia-public", path);
  }
  // Other hosts keep every other page, and show the Sernia pages at the same paths.
  assert.equal(rootRequestMode("https://eesposito.com/"), "app");
  assert.equal(rootRequestMode("http://localhost:5293/sernia-chat"), "app");
  assert.equal(rootRequestMode("http://localhost:5293/privacy"), "sernia-public");
});

test("on a Sernia host the /api proxy forwards only the public booking API", () => {
  for (const [host, url] of [
    ["serniacapital.com", "/api/showing-bookings/public/units/320-03"],
    ["serniacapital.com", "/api/showing-bookings/public/units?listing=320-03"],
    ["www.serniacapital.com:443", "/api/showing-bookings/public/bookings"],
    ["serniacapital.localhost:5293", "/api/showing-bookings/public/a/../b"],
  ]) {
    assert.equal(isApiProxyAllowed(host, url), true, `${host}${url}`);
  }
  for (const url of [
    "/api",
    "/api/",
    "/api/sernia-ai/chat",
    "/api/ai-demos/chat-emilio",
    "/api/showing-bookings/settings",
    "/api/showing-bookings/public",
    "/API/showing-bookings/public/units",
    "/api/showing-bookings/public/../settings",
    "/api/showing-bookings/public/%2e%2e/%2e%2e/sernia-ai/chat",
    "/api/sernia-ai/../showing-bookings/public/units",
    "/api/showing-bookings/publicity",
    "http://example.test/api/sernia-ai/chat",
  ]) {
    assert.equal(isApiProxyAllowed("serniacapital.com", url), false, url);
  }
  // Encoded slashes stay inside one segment: fetch() forwards this path as is,
  // and the API can only route it under the public prefix.
  assert.equal(isApiProxyAllowed("serniacapital.com", "/api/showing-bookings/public/..%2F..%2Fsernia-ai"), true);
});

test("on any other host the /api proxy forwards every path, as before", () => {
  for (const host of ["eesposito.com", "localhost:5293", "react-router-portfolio-pr-123.up.railway.app", undefined]) {
    for (const url of ["/api/sernia-ai/chat", "/api/showing-bookings/public/units", "/api/showing-bookings/settings"]) {
      assert.equal(isApiProxyAllowed(host, url), true, `${host}${url}`);
    }
  }
});

test("every page under the Sernia public layout is at a Sernia public path", () => {
  const routeFiles = readdirSync(new URL("../../app/routes/", import.meta.url)).filter(
    (name) => name.startsWith("_sernia-public.") && name !== "_sernia-public.tsx",
  );
  assert.ok(routeFiles.length >= 2, "expected the privacy and SMS terms pages");
  for (const name of routeFiles) {
    const segments = name
      .replace(/\.(tsx|ts|jsx|js)$/, "")
      .split(".")
      .slice(1)
      .filter((segment) => segment !== "_index")
      .map((segment) => (segment.startsWith("$") ? "sample" : segment.replace(/^\((.*)\)$/, "$1")));
    const path = `/${segments.join("/")}`;
    assert.equal(isSerniaPublicPath(path), true, `${name} is served at ${path}`);
  }
});
