import assert from "node:assert/strict";
import test from "node:test";

import {
  STATUS_LABELS,
  bookedThrough,
  changeLine,
  dayAt,
  factLabel,
  fullTime,
  isLeadsPath,
  leadThreadPath,
  listTime,
  orderedFacts,
  refusal,
  runResult,
  showingLabel,
  showingState,
  waitedFor,
  waitsOnTeam,
} from "~/sites/team/lib/lead-inbox";
import { LEAD_TABS } from "~/sites/team/lib/team-sections";

// The leads list and a lead's page, without React: paths, times and words.

test("a lead's page is at the path calendar events and notifications link to", () => {
  assert.equal(leadThreadPath("1a2b3c4d5e6f7a8b"), "/team/leads/threads/1a2b3c4d5e6f7a8b");
  assert.equal(leadThreadPath("a/b c"), "/team/leads/threads/a%2Fb%20c");
});

test("the Leads tab is first, and is current on the list and on a lead's page", () => {
  assert.deepEqual(LEAD_TABS[0], { url: "/team/leads", title: "Leads" });
  assert.equal(LEAD_TABS.at(-1)?.url, "/team/leads/overview");
  assert.equal(isLeadsPath("/team/leads"), true);
  assert.equal(isLeadsPath("/team/leads/"), true);
  assert.equal(isLeadsPath("/team/leads/threads/abc"), true);
  assert.equal(isLeadsPath("/team/leads/bookings"), false);
  assert.equal(isLeadsPath("/team/leads/overview"), false);
});

test("times read as Gmail's list reads them, in Eastern time", () => {
  const now = new Date("2026-11-28T03:00:00Z"); // Nov 27, 10 PM Eastern
  assert.equal(listTime("2026-11-28T02:49:00Z", now), "9:49 PM");
  // Earlier the same Eastern day, though a different day in UTC.
  assert.equal(listTime("2026-11-27T14:05:00Z", now), "9:05 AM");
  assert.equal(listTime("2026-11-26T22:00:00Z", now), "Nov 26");
  assert.equal(listTime("2025-11-26T22:00:00Z", now), "11/26/25");
  assert.equal(listTime(null, now), "");
  assert.equal(fullTime("2026-11-29T18:30:00Z"), "Sun, Nov 29, 1:30 PM");
  assert.equal(waitedFor("2026-11-28T02:55:00Z", now), "5 min");
  assert.equal(waitedFor("2026-11-27T22:00:00Z", now), "5 h");
  assert.equal(waitedFor("2026-11-24T22:00:00Z", now), "3 days");
});

test("only a draft or a question waits on the team, and every status has words", () => {
  assert.equal(waitsOnTeam("draft_waiting"), true);
  assert.equal(waitsOnTeam("needs_team"), true);
  for (const status of ["handed_off", "replied", "draft_rejected", "no_reply_needed", "handled"] as const) {
    assert.equal(waitsOnTeam(status), false);
    assert.ok(STATUS_LABELS[status]);
  }
});

test("a thread's showing reads in a few words", () => {
  const starts_at = "2026-11-29T18:30:00Z";
  assert.equal(showingLabel({ state: "booked", unit: "12-3", starts_at }), "Booked Sun, Nov 29, 1:30 PM");
  assert.equal(showingLabel({ state: "held", unit: "12-3", starts_at, attendance: "attended" }), "Toured Sun, Nov 29, 1:30 PM");
  assert.equal(showingLabel({ state: "held", unit: "12-3", starts_at, attendance: "unknown" }), "Showing was Sun, Nov 29, 1:30 PM");
  assert.equal(
    showingLabel({ state: "cancelled", unit: "12-3", starts_at, cancelled_by: "prospect" }),
    "Cancelled: Sun, Nov 29, 1:30 PM",
  );
  assert.equal(showingState({ status: "booked", past: false, attendance: "unknown", cancelled_by: null }), "Booked");
  assert.equal(showingState({ status: "booked", past: true, attendance: "no_show", cancelled_by: null }), "Did not come");
  assert.equal(showingState({ status: "cancelled", past: false, attendance: "unknown", cancelled_by: "team" }), "Cancelled by the team");
  assert.equal(bookedThrough("page"), "on the booking page");
});

test("each run says how it ended and who decided", () => {
  const at = "2026-11-28T02:54:00Z";
  assert.equal(runResult({ result: "sent", decided_by: "Pat Doe", decided_at: at }), "Approved by Pat Doe and sent, Fri, Nov 27, 9:54 PM");
  assert.equal(runResult({ result: "sent_edited", decided_by: "Pat Doe", decided_at: null }), "Edited by Pat Doe and sent");
  assert.equal(runResult({ result: "waiting", decided_by: null, decided_at: null }), "Waiting for approval");
  assert.equal(runResult({ result: "sent_automatically", decided_by: null, decided_at: at }), "Sent automatically, Fri, Nov 27, 9:54 PM");
  assert.equal(runResult({ result: "captured", decided_by: "Pat Doe", decided_at: null }), "Approved by Pat Doe, captured, not sent");
});

test("the header leads with move-in, pets and occupants", () => {
  const facts = orderedFacts([
    { label: "Credit score", value: "700+" },
    { label: "Number of Occupants", value: "2" },
    { label: "Move in", value: "Jan 01, 2027" },
    { label: "Pets", value: "Yes" },
  ]);
  assert.deepEqual(
    facts.map((fact) => factLabel(fact.label)),
    ["Move in", "Pets", "Occupants", "Credit score"],
  );
});

test("a refusal reads as a sentence", () => {
  assert.equal(refusal(404, { detail: "No lead thread with that id." }, "Could not load"), "No lead thread with that id.");
  assert.equal(refusal(403, null, "Could not load"), "Sign in again with your Sernia account.");
  assert.equal(refusal(502, null, "Could not load"), "Could not load (502).");
  assert.equal(refusal(0, null, "Could not load"), "Could not load. Check your connection.");
});

test("a change after a draft was written says who, which unit and when", () => {
  assert.equal(dayAt("2026-11-29T18:30:00Z"), "Sun, Nov 29 at 1:30 PM");
  assert.equal(
    changeLine({ kind: "booked", unit: "12-3", starts_at: "2026-11-29T18:30:00Z", booked_by: "Jordan", at: "2026-11-28T02:48:00Z" }),
    "Jordan booked 12-3 for Sun, Nov 29 at 1:30 PM after this draft was written.",
  );
  assert.equal(
    changeLine({
      kind: "cancelled",
      unit: "12-3",
      starts_at: "2026-11-29T18:30:00Z",
      booked_by: "Jordan",
      at: "2026-11-28T03:00:00Z",
      cancelled_by: "prospect",
    }),
    "The showing of 12-3 on Sun, Nov 29 at 1:30 PM was cancelled by the lead after this draft was written.",
  );
});
