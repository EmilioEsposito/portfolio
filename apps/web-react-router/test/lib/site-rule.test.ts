import assert from "node:assert/strict";
import test from "node:test";

import {
  isApiProxyAllowed,
  isTeamPath,
  rootRequestMode,
  TEAM_API_PATHS,
  TEAM_HOME_URL,
  teamHref,
  teamRedirect,
  wwwTwinHost,
  teamSiteServed,
} from "~/sites/site";

import { routeTable, sampleUrl } from "./route-table";

// The team's pages (/team and /team/*) in the site rule: on which hosts and in
// which environments they are served, the www redirect, and the API paths a
// Sernia host forwards for them.

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

const TEAM_PATHS = ["/team", "/team/", "/team/chat", "/team/leads/bookings", "/TEAM/Chat", "/te%61m/chat", "/team/unknown"];
const NOT_TEAM_PATHS = ["/", "/teams", "/team-chat", "/teamx/chat", "/showings/team", "/sernia-chat", "/api/team"];

test("team paths are /team and anything under /team/, ignoring case and encoding", () => {
  for (const path of TEAM_PATHS) assert.equal(isTeamPath(path), true, path);
  for (const path of NOT_TEAM_PATHS) assert.equal(isTeamPath(path), false, path);
});

test("on a Sernia host a team path is the team site, in every environment and whether or not the public pages are on", () => {
  for (const railwayEnvironmentName of [undefined, "production", "development", "portfolio-pr-435"]) {
    for (const pagesEnabled of [true, false]) {
      for (const origin of ["https://serniacapital.com", "https://www.serniacapital.com", "http://serniacapital.localhost:5293"]) {
        for (const path of TEAM_PATHS) {
          const mode = rootRequestMode(`${origin}${path}`, { railwayEnvironmentName, pagesEnabled });
          assert.equal(mode, "team", `${origin}${path} in ${railwayEnvironmentName}, pages ${pagesEnabled}`);
        }
      }
    }
  }
});

test("on another host a team path is the team site outside production only", () => {
  for (const origin of [
    "http://localhost:5293",
    "http://127.0.0.1:5293",
    "https://dev.eesposito.com",
    "https://react-router-portfolio-pr-435.up.railway.app",
    "https://eesposito.com",
  ]) {
    for (const railwayEnvironmentName of [undefined, "", "development", "portfolio-pr-435"]) {
      assert.equal(rootRequestMode(`${origin}/team/chat`, { railwayEnvironmentName }), "team", `${origin} ${railwayEnvironmentName}`);
    }
    // In production it is the portfolio's, which has no such page: its 404.
    for (const railwayEnvironmentName of ["production", " Production "]) {
      assert.equal(rootRequestMode(`${origin}/team/chat`, { railwayEnvironmentName }), "portfolio", origin);
      assert.equal(rootRequestMode(`${origin}/team`, { railwayEnvironmentName }), "portfolio", origin);
    }
  }
});

test("the environment decides when no option is given", () => {
  assert.equal(rootRequestMode("https://eesposito.com/team"), "team");
  withEnvironment({ RAILWAY_ENVIRONMENT_NAME: "production" }, () => {
    assert.equal(rootRequestMode("https://eesposito.com/team"), "portfolio");
    assert.equal(rootRequestMode("https://serniacapital.com/team"), "team");
    assert.equal(teamSiteServed("eesposito.com"), false);
    assert.equal(teamSiteServed("serniacapital.com"), true);
  });
  withEnvironment({ RAILWAY_ENVIRONMENT_NAME: "development" }, () => {
    assert.equal(rootRequestMode("https://dev.eesposito.com/team/chat"), "team");
  });
  withEnvironment({ RAILWAY_ENVIRONMENT_NAME: "production", SERNIA_PUBLIC_HOSTS: "sernia.example.test" }, () => {
    assert.equal(rootRequestMode("https://sernia.example.test/team"), "team");
    assert.equal(rootRequestMode("https://serniacapital.com/team"), "portfolio");
  });
});

test("only team paths are the team site", () => {
  for (const origin of ["https://serniacapital.com", "http://localhost:5293", "https://eesposito.com"]) {
    for (const path of NOT_TEAM_PATHS) {
      assert.notEqual(rootRequestMode(`${origin}${path}`), "team", `${origin}${path}`);
    }
  }
});

test("a team path on www of a Sernia host moves to the host without www", () => {
  // No scheme: the browser keeps https, though the server behind the proxy sees http.
  assert.equal(teamRedirect("https://www.serniacapital.com/team"), "//serniacapital.com/team");
  assert.equal(teamRedirect("http://www.serniacapital.com/team"), "//serniacapital.com/team");
  assert.equal(
    teamRedirect("https://www.serniacapital.com/team/chat?id=conversation-1#end"),
    "//serniacapital.com/team/chat?id=conversation-1#end",
  );
  assert.equal(teamRedirect("https://WWW.SerniaCapital.com:8443/team/leads"), "//serniacapital.com:8443/team/leads");
  // The public pages answer on www as they are; the bare host and other hosts never move.
  for (const url of [
    "https://www.serniacapital.com/",
    "https://www.serniacapital.com/privacy",
    "https://www.serniacapital.com/teams",
    "https://serniacapital.com/team",
    "https://www.eesposito.com/team",
    "http://localhost:5293/team",
  ]) {
    assert.equal(teamRedirect(url), null, url);
  }
  // Only between two Sernia hosts.
  assert.equal(teamRedirect("https://www.sernia.example.test/team", { hostsSetting: "www.sernia.example.test" }), null);
  assert.equal(
    teamRedirect("https://www.sernia.example.test/team", { hostsSetting: "sernia.example.test,www.sernia.example.test" }),
    "//sernia.example.test/team",
  );
});

test("links to the team go to /team where it is served, and to serniacapital.com elsewhere", () => {
  assert.equal(teamHref("https://serniacapital.com/privacy", { railwayEnvironmentName: "production" }), "/team");
  assert.equal(teamHref("http://localhost:5293/privacy", { railwayEnvironmentName: undefined }), "/team");
  assert.equal(teamHref("https://eesposito.com/privacy", { railwayEnvironmentName: "production" }), TEAM_HOME_URL);
  assert.equal(TEAM_HOME_URL, "https://serniacapital.com/team");
});

// Why each API path is forwarded on a Sernia host: a call one of the team's
// pages makes. One example per entry.
const TEAM_CALLS: Record<(typeof TEAM_API_PATHS)[number], string[]> = {
  "/api/sernia-ai/": [
    "/api/sernia-ai/chat",
    "/api/sernia-ai/conversations/history?limit=50",
    "/api/sernia-ai/conversation/abc/messages",
    "/api/sernia-ai/admin/settings",
    "/api/sernia-ai/push/subscribe",
  ],
  "/api/showing-bookings/": [
    "/api/showing-bookings/settings",
    "/api/showing-bookings/calendar/connections",
    "/api/showing-bookings/calendar/callback?code=made-up&state=made-up",
    "/api/showing-bookings/showings/12/cancel",
    "/api/showing-bookings/public/units",
  ],
  "/api/rental-listings": ["/api/rental-listings"],
  "/api/rental-listings/": ["/api/rental-listings/history?limit=50", "/api/rental-listings/listings/900-01"],
  "/api/zillow-leads/settings": ["/api/zillow-leads/settings"],
  "/api/zillow-leads/threads": ["/api/zillow-leads/threads?limit=100"],
  "/api/zillow-leads/threads/": ["/api/zillow-leads/threads/1a2b3c4d5e6f7a8b"],
  "/api/team-tasks": ["/api/team-tasks"],
  "/api/team-tasks/": ["/api/team-tasks/clickup/86abc1/status", "/api/team-tasks/quo/TKone/status"],
  "/api/contacts/slug/sernia": ["/api/contacts/slug/sernia"],
  "/api/open_phone/tenants": ["/api/open_phone/tenants"],
  "/api/open_phone/send_message": ["/api/open_phone/send_message"],
  "/api/schedulers/": ["/api/schedulers/get_jobs", "/api/schedulers/run_job_now/apscheduler/job-1"],
};

test("on a Sernia host the /api proxy forwards the public booking API and each path the team's pages call", () => {
  assert.deepEqual(Object.keys(TEAM_CALLS).sort(), [...TEAM_API_PATHS].sort());
  for (const host of ["serniacapital.com", "www.serniacapital.com:443", "serniacapital.localhost:5293"]) {
    for (const calls of Object.values(TEAM_CALLS)) {
      for (const url of calls) assert.equal(isApiProxyAllowed(host, url), true, `${host}${url}`);
    }
  }
});

test("on a Sernia host the /api proxy refuses everything else, `..` tricks included", () => {
  for (const url of [
    "/api",
    "/api/",
    "/api/docs",
    "/api/openapi.json",
    "/api/ai-demos/chat-emilio",
    "/api/ai-demos/multi-agent-chat",
    "/api/agent-conversations/conversations/history",
    "/api/open_phone/contacts",
    "/api/open_phone/create_contacts_in_openphone",
    "/api/open_phone/tenants/extra",
    "/api/contacts/",
    "/api/contacts/slug/other",
    "/api/zillow-leads/queue",
    "/api/zillow-leads/settings/",
    "/api/rental-listingsx",
    "/api/sernia-ai",
    "/api/sernia-aix/chat",
    "/api/push/subscribe",
    "/api/cron/run",
    "/api/google/auth",
    "/API/sernia-ai/chat",
    "/api/sernia-ai/../open_phone/contacts",
    "/api/sernia-ai/%2e%2e/open_phone/contacts",
    "/api/sernia-ai/..\\open_phone\\contacts",
    "/api/schedulers/../docs",
    "/api/contacts/slug/sernia/../../",
    "/api/open_phone/tenants/../contacts",
    "/api/zillow-leads/settings/../queue",
    "/api/zillow-leads/threads/../queue",
    "/api/zillow-leads/threadsx",
    "/api/showing-bookings/public/../../docs",
    "/api/open_phone/../sernia-ai/chat",
    "http://example.test/api/sernia-ai/chat",
    "//example.test/api/sernia-ai/chat",
  ]) {
    assert.equal(isApiProxyAllowed("serniacapital.com", url), false, url);
  }
  // `..` that stays inside an allowed prefix is allowed: fetch() forwards the resolved path.
  assert.equal(isApiProxyAllowed("serniacapital.com", "/api/showing-bookings/public/../settings"), true);
  // Encoded slashes stay inside one segment: fetch() forwards this path as is,
  // and the API can only route it under the prefix it starts with.
  assert.equal(isApiProxyAllowed("serniacapital.com", "/api/showing-bookings/public/..%2F..%2Fsernia-ai"), true);
});

test("on any other host the /api proxy forwards every path, as before", () => {
  for (const host of ["eesposito.com", "localhost:5293", "react-router-portfolio-pr-123.up.railway.app", undefined]) {
    for (const url of ["/api/sernia-ai/chat", "/api/ai-demos/chat-emilio", "/api/docs", "/api/open_phone/contacts"]) {
      assert.equal(isApiProxyAllowed(host, url), true, `${host}${url}`);
    }
  }
});

// The routes as app/routes.ts declares them.
const TEAM_LAYOUT = "sites/team/routes/layout.tsx";
const routes = routeTable();

test("every team route is under /team, inside the team layout", () => {
  const team = routes.filter((route) => route.file.startsWith("sites/team/") && route.file !== TEAM_LAYOUT);
  assert.ok(team.length >= 11, `expected the team's pages, found ${team.length}`);
  for (const route of team) {
    assert.equal(isTeamPath(sampleUrl(route.path)), true, `${route.file} is served at ${route.path}`);
    assert.ok(route.parents.includes(TEAM_LAYOUT), `${route.file} must be inside the team layout`);
  }
});

test("nothing else is under /team", () => {
  for (const route of routes) {
    if (route.file === TEAM_LAYOUT || !isTeamPath(sampleUrl(route.path))) continue;
    assert.ok(route.file.startsWith("sites/team/routes/"), `${route.file} is at ${route.path}`);
    assert.ok(route.parents.includes(TEAM_LAYOUT), `${route.file} must be inside the team layout`);
  }
});

test("the team's pages are at the addresses the backend links to", () => {
  // api/src/utils/team_links.py builds every staff link from these paths.
  const paths = routes.filter((route) => route.parents.includes(TEAM_LAYOUT)).map((route) => route.path);
  for (const path of [
    "/team",
    "/team/chat",
    "/team/tasks",
    "/team/settings",
    "/team/admin",
    "/team/tenants",
    "/team/scheduler",
    "/team/leads",
    "/team/leads/listings",
    "/team/leads/settings",
    "/team/leads/bookings",
    "/team/leads/overview",
    // A lead's page: calendar events and notifications link here.
    "/team/leads/threads/:threadId",
  ]) {
    assert.ok(paths.includes(path), path);
  }
  // The old staff addresses are gone, with no redirects.
  for (const path of [
    "/sernia-chat",
    "/sernia-settings",
    "/sernia-admin",
    "/message-tenants",
    "/scheduler",
    "/systems/leasing-leads/listings",
    "/systems/leasing-leads/settings",
    "/systems/leasing-leads/bookings",
  ]) {
    assert.equal(routes.some((route) => route.path === path), false, path);
  }
  // The write-up stays a public portfolio page.
  const writeUp = routes.find((route) => route.path === "/systems/leasing-leads");
  assert.equal(writeUp?.file, "sites/portfolio/routes/systems/leasing-leads.tsx");
  assert.deepEqual(writeUp?.parents, []);
});

test("a Sernia host names its www twin when Sernia serves both", () => {
  assert.equal(wwwTwinHost("https://serniacapital.com/team"), "www.serniacapital.com");
  assert.equal(wwwTwinHost("https://SerniaCapital.com:8443/team/chat?id=1"), "www.serniacapital.com:8443");
  for (const url of [
    // The twin itself, a Sernia host with no twin, and a host that is not Sernia's.
    "https://www.serniacapital.com/team",
    "http://serniacapital.localhost:5293/team",
    "https://eesposito.com/team",
  ]) {
    assert.equal(wwwTwinHost(url), null, url);
  }
  assert.equal(
    wwwTwinHost("https://sernia.example.test/team", { hostsSetting: "sernia.example.test,www.sernia.example.test" }),
    "www.sernia.example.test",
  );
});
