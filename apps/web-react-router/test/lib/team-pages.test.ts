import assert from "node:assert/strict";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { retireRootServiceWorker } from "~/lib/retire-root-service-worker";
import { TeamChecking, TeamSignIn, TeamWrongAccount } from "~/sites/team/components/team-gate";
import { TEAM_SERVICE_WORKER_SCOPE, TEAM_SERVICE_WORKER_URL } from "~/sites/team/lib/push-subscription";
import { endsPush, isTeamAdmin, teamAccess, teamEmails } from "~/sites/team/lib/team-access";
import {
  approvalsFrom,
  approvalsText,
  conversationHref,
  openText,
  taskCounts,
  tasksText,
} from "~/sites/team/lib/team-home";
import type { TeamTask } from "~/sites/team/lib/team-tasks";
import { isCurrentSection, TEAM_HOME, TEAM_SECTIONS, visibleSections } from "~/sites/team/lib/team-sections";
import type { ConversationSummary } from "~/sites/team/lib/sernia-conversations";

// The team's guard, home page, navigation and installed app, without Clerk.

const verified = (address: string) => ({ address, verified: true });

test("the guard waits for Clerk, then lets in a verified @serniacapital.com address only", () => {
  assert.equal(teamAccess({ authLoaded: false, signedIn: undefined, emails: undefined }), "loading");
  assert.equal(teamAccess({ authLoaded: true, signedIn: false, emails: undefined }), "signed-out");
  // Signed in, but the user's addresses have not loaded yet.
  assert.equal(teamAccess({ authLoaded: true, signedIn: true, emails: undefined }), "loading");
  assert.equal(teamAccess({ authLoaded: true, signedIn: true, emails: [verified("ana@serniacapital.com")] }), "allowed");
  assert.equal(teamAccess({ authLoaded: true, signedIn: true, emails: [verified("Ana@SerniaCapital.com ")] }), "allowed");
  assert.equal(
    teamAccess({ authLoaded: true, signedIn: true, emails: [verified("ana@example.com"), verified("ana@serniacapital.com")] }),
    "allowed",
  );
  for (const emails of [
    [],
    [verified("ana@example.com")],
    [{ address: "ana@serniacapital.com", verified: false }],
    [verified("ana@serniacapital.com.example.com")],
    [verified("ana@notserniacapital.com")],
  ]) {
    assert.equal(teamAccess({ authLoaded: true, signedIn: true, emails }), "wrong-account", JSON.stringify(emails));
  }
});

test("Clerk's addresses are read with their verification", () => {
  assert.equal(teamEmails(undefined), undefined);
  assert.equal(teamEmails(null), undefined);
  assert.deepEqual(
    teamEmails({
      emailAddresses: [
        { emailAddress: "ana@serniacapital.com", verification: { status: "verified" } },
        { emailAddress: "ana@example.com", verification: { status: "unverified" } },
        { emailAddress: "bo@example.com", verification: null },
      ],
    }),
    [
      { address: "ana@serniacapital.com", verified: true },
      { address: "ana@example.com", verified: false },
      { address: "bo@example.com", verified: false },
    ],
  );
});

test("the signed-out page has the wordmark, the one line and a sign-in button", () => {
  const html = renderToStaticMarkup(createElement(TeamSignIn, { onSignIn: () => {}, opening: false, failed: false }));
  assert.match(html, /Sernia Capital/);
  assert.match(html, /This area is for the Sernia Capital team\./);
  assert.match(html, /<button[^>]*>Sign in<\/button>/);
  assert.doesNotMatch(html, / disabled=""/);
});

test("pressing sign-in shows at once that it is opening, and says so if it cannot load", () => {
  const opening = renderToStaticMarkup(createElement(TeamSignIn, { onSignIn: () => {}, opening: true, failed: false }));
  assert.match(opening, /Opening sign-in…/);
  assert.match(opening, / disabled=""/);
  const failed = renderToStaticMarkup(createElement(TeamSignIn, { onSignIn: () => {}, opening: true, failed: true }));
  assert.match(failed, /Sign-in could not load/);
  assert.match(failed, /<button[^>]*>Sign in<\/button>/);
});

test("an account that is not the team's is told so and can sign out", () => {
  const html = renderToStaticMarkup(
    createElement(TeamWrongAccount, { address: "ana@example.com", onSignOut: () => {}, signingOut: false }),
  );
  assert.match(html, /not on the Sernia Capital team/);
  assert.match(html, /You are signed in as ana@example.com\./);
  assert.match(html, /@serniacapital\.com/);
  assert.match(html, /<button[^>]*>Sign out<\/button>/);
  const signingOut = renderToStaticMarkup(
    createElement(TeamWrongAccount, { address: null, onSignOut: () => {}, signingOut: true }),
  );
  assert.match(signingOut, /Signing out…/);
  assert.match(renderToStaticMarkup(createElement(TeamChecking)), /Checking sign-in/);
});

test("the admin's pages show only to the admin", () => {
  assert.equal(isTeamAdmin("emilio@serniacapital.com"), true);
  assert.equal(isTeamAdmin("Emilio@SerniaCapital.com"), true);
  assert.equal(isTeamAdmin("ana@serniacapital.com"), false);
  assert.equal(isTeamAdmin(undefined), false);
  const everyone = visibleSections(false).map((section) => section.url);
  const admin = visibleSections(true).map((section) => section.url);
  assert.deepEqual(admin, TEAM_SECTIONS.map((section) => section.url));
  assert.ok(!everyone.includes("/team/settings") && !everyone.includes("/team/admin"));
  for (const url of ["/team/chat", "/team/leads", "/team/tenants", "/team/scheduler"]) {
    assert.ok(everyone.includes(url), url);
  }
  // Tasks come first, on the team home and in the navigation (after Team home).
  assert.equal(everyone[0], "/team/tasks");
  assert.equal(admin[0], "/team/tasks");
  assert.ok(admin.includes("/team/push-devices") && !everyone.includes("/team/push-devices"));
  for (const section of TEAM_SECTIONS) assert.ok(section.description.length > 0, section.url);
});

test("the navigation marks the section the page is in", () => {
  const leads = TEAM_SECTIONS.find((section) => section.url === "/team/leads")!;
  assert.equal(isCurrentSection(leads, "/team/leads"), true);
  assert.equal(isCurrentSection(leads, "/team/leads/bookings"), true);
  assert.equal(isCurrentSection(leads, "/team/leadsx"), false);
  assert.equal(isCurrentSection(TEAM_HOME, "/team"), true);
  assert.equal(isCurrentSection(TEAM_HOME, "/team/"), true);
  assert.equal(isCurrentSection(TEAM_HOME, "/team/chat"), false);
});

function conversation(id: string, hasPending: boolean, updatedAt: string | null): ConversationSummary {
  return {
    conversation_id: id,
    modality: "web_chat",
    preview: `Preview ${id}`,
    has_pending: hasPending,
    trigger_source: null,
    trigger_contact_name: null,
    created_at: "2026-11-01T12:00:00Z",
    updated_at: updatedAt,
  };
}

test("the team home lists the conversations with something to approve, most recent first", () => {
  // The pending list answers only conversations with an approval waiting.
  const conversations = [
    conversation("a", true, "2026-11-20T10:00:00Z"),
    conversation("c", true, "2026-11-22T09:00:00Z"),
    conversation("d", true, null),
    conversation("e", true, "2026-11-21T10:00:00Z"),
  ];
  const approvals = approvalsFrom(conversations, 3);
  assert.equal(approvals.count, 4);
  assert.deepEqual(approvals.first.map((item) => item.conversation_id), ["c", "e", "a"]);
  // Every one is marked in the chat's list, not only those the home shows.
  assert.deepEqual(approvals.ids, ["c", "e", "a", "d"]);
  // One that says it has nothing waiting is left out.
  assert.deepEqual(approvalsFrom([conversation("b", false, null)]), { count: 0, first: [], ids: [] });
  assert.equal(approvalsText(0), "Nothing to approve");
  assert.equal(approvalsText(2), "2 to approve");
  assert.equal(conversationHref("ai_sms_from_+14125550123"), "/team/chat?id=ai_sms_from_%2B14125550123");
});

function task(id: string, due: string | null, completed = false): TeamTask {
  return { source: "clickup", id, title: id, description: "", due, assignees: [], status: "to do", completed, url: null };
}

test("the Tasks badge counts open tasks, and the overdue ones by the reader's calendar day", () => {
  const now = new Date("2026-11-24T15:00:00Z");
  const view = {
    tasks: [
      task("late", "2026-11-20T12:00:00Z"),
      // Late on the 23rd in New York, the 24th in UTC.
      task("yesterday-in-new-york", "2026-11-24T03:00:00Z"),
      task("today", "2026-11-24T20:00:00Z"),
      task("undated", null),
      // Completed on the Tasks page: struck through there, not counted.
      task("done", "2026-11-01T12:00:00Z", true),
    ],
    unavailable: [],
    incomplete: [],
  };
  const counts = taskCounts(view, now, "America/New_York");
  assert.deepEqual(counts, { open: 4, overdue: 2, partial: [] });
  assert.equal(tasksText(counts), "4 open, 2 overdue");
  assert.equal(taskCounts(view, now, "UTC").overdue, 1);

  // A source not read whole: the count is at least this many.
  const partial = taskCounts({ tasks: [task("a", null)], unavailable: ["quo"], incomplete: ["quo"] }, now);
  assert.deepEqual(partial.partial, ["quo"]);
  assert.equal(openText(partial), "1+ open");
  assert.equal(openText(taskCounts({ tasks: [], unavailable: [], incomplete: [] }, now)), "No open tasks");
  assert.equal(openText(taskCounts({ tasks: [], unavailable: ["clickup"], incomplete: [] }, now)), "Tasks not read");
});

const PUBLIC = new URL("../../public/", import.meta.url);

test("the installed app and its service worker belong to /team", () => {
  assert.equal(TEAM_SERVICE_WORKER_URL, "/team/sw.js");
  assert.equal(TEAM_SERVICE_WORKER_SCOPE, "/team/");
  assert.ok(existsSync(new URL("team/sw.js", PUBLIC)));
  // Nothing is served at the old root addresses.
  assert.equal(existsSync(new URL("sw.js", PUBLIC)), false);
  assert.equal(existsSync(new URL("manifest.json", PUBLIC)), false);
  const manifest = JSON.parse(readFileSync(new URL("team/manifest.json", PUBLIC), "utf8"));
  // The installed app opens the team home. Its id stays /team, so an app
  // installed when it opened the chat (Android, desktop) takes the new address.
  assert.equal(manifest.start_url, "/team");
  assert.equal(manifest.id, "/team");
  // Without the trailing slash, so the installed app also counts the team home
  // at /team as its own page.
  assert.equal(manifest.scope, "/team");
  // A link the system hands to the installed app (Android for links in scope;
  // desktop Chrome and Edge when allowed) reuses the app's open window.
  assert.deepEqual(manifest.launch_handler, { client_mode: ["navigate-existing", "auto"] });
  // A browser offers to install only with icons of the sizes it asks for,
  // and each is the size the manifest says it is (read from the PNG header).
  for (const size of [192, 512]) {
    const icon = manifest.icons.find((entry: { sizes: string }) => entry.sizes === `${size}x${size}`);
    assert.ok(icon, `a ${size}px icon`);
    const png = readFileSync(new URL(icon.src.slice(1), PUBLIC));
    assert.equal(png.readUInt32BE(16), size);
    assert.equal(png.readUInt32BE(20), size);
  }
});

test("each team page with its own sidebar places the notifications banner itself", () => {
  // The team layout places it for every other page; these fill the screen
  // with their own shell, so the layout leaves it to them.
  const routes = new URL("../../app/sites/team/routes/", import.meta.url);
  const own = readdirSync(routes, { recursive: true, encoding: "utf8" })
    .filter((file) => file.endsWith(".tsx"))
    .filter((file) => readFileSync(new URL(file, routes), "utf8").includes("ownSidebar: true"));
  assert.deepEqual(own.sort(), ["admin.tsx", "chat.tsx", "settings.tsx"]);
  for (const file of own) {
    const source = readFileSync(new URL(file, routes), "utf8");
    assert.ok(source.includes("<TeamDeviceBanner />"), file);
    // On a phone, where their sidebar is closed, the header links to the team home.
    assert.ok(source.includes("<TeamHomeButton />"), file);
  }
});

type FakeRegistration = {
  scope: string;
  pushManager: { getSubscription(): Promise<{ unsubscribe(): Promise<boolean> } | null> };
  unregister(): Promise<boolean>;
};

function registration(scope: string, events: string[], subscribed = true): FakeRegistration {
  return {
    scope,
    pushManager: {
      getSubscription: async () => (subscribed ? { unsubscribe: async () => (events.push(`unsubscribe ${scope}`), true) } : null),
    },
    unregister: async () => (events.push(`unregister ${scope}`), true),
  };
}

test("the old staff app's root service worker is retired, and the team's is left alone", async () => {
  const events: string[] = [];
  const container = {
    getRegistrations: async () => [
      registration("https://example.test/team/", events),
      registration("https://example.test/", events),
    ],
  } as unknown as ServiceWorkerContainer;
  assert.equal(await retireRootServiceWorker(container, "https://example.test"), 1);
  assert.deepEqual(events, ["unsubscribe https://example.test/", "unregister https://example.test/"]);
});

test("retiring does nothing where no such worker is, or where there are no service workers", async () => {
  const events: string[] = [];
  const onlyTeam = { getRegistrations: async () => [registration("https://example.test/team/", events)] };
  assert.equal(await retireRootServiceWorker(onlyTeam as unknown as ServiceWorkerContainer, "https://example.test"), 0);
  assert.deepEqual(events, []);
  assert.equal(await retireRootServiceWorker(undefined, "https://example.test"), 0);
  const unsubscribed = { getRegistrations: async () => [registration("https://example.test/", events, false)] };
  assert.equal(await retireRootServiceWorker(unsubscribed as unknown as ServiceWorkerContainer, "https://example.test"), 1);
  assert.deepEqual(events, ["unregister https://example.test/"]);
});

test("a browser's notifications end once no team member is signed in, and never while loading", () => {
  assert.equal(endsPush("signed-out"), true);
  assert.equal(endsPush("wrong-account"), true);
  assert.equal(endsPush("loading"), false);
  assert.equal(endsPush("allowed"), false);
});
