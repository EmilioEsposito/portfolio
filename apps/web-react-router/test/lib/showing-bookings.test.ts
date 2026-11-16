import assert from "node:assert/strict";
import test from "node:test";

import {
  type BookingSettings,
  type DrainReport,
  CONNECT_RESULTS,
  STATUS_LABELS,
  cleanSettings,
  connectResult,
  connectionActions,
  describeLastRead,
  describeDrain,
  describeGuard,
  describeMode,
  formatWhen,
  humanize,
  needsAttention,
  parseUnits,
  cleanAgent,
  clock,
  describeAttention,
  describeOffer,
  describeRemoval,
  describeShowingCancel,
  describeWindows,
  newAgent,
  parseCalendars,
  refusalMessage,
  refusalProblems,
  sameSettings,
  startsLiveSends,
} from "../../app/lib/showing-bookings.ts";

const settings = (patch: Partial<BookingSettings> = {}): BookingSettings => ({
  send_mode: "capture",
  live_units: [],
  test_sends_enabled: false,
  lead_agent_uses_open_times: false,
  ...patch,
});

const report = (patch: Partial<DrainReport> = {}): DrainReport => ({
  captured: 0,
  sent: 0,
  failed: 0,
  unknown: 0,
  skipped: 0,
  retrying: 0,
  interrupted: 0,
  ...patch,
});

test("units are read from commas or lines, trimmed, without repeats in any case", () => {
  assert.deepEqual(parseUnits(" 320-03, 12-A\n12-a ,, 320-04 "), ["320-03", "12-A", "320-04"]);
  assert.deepEqual(parseUnits("   "), []);
});

test("two settings that differ only in spacing or repeated units are the same", () => {
  assert.ok(sameSettings(settings({ live_units: ["320-03", " 320-03"] }), settings({ live_units: ["320-03"] })));
  assert.ok(!sameSettings(settings(), settings({ send_mode: "live" })));
  assert.deepEqual(cleanSettings(settings({ live_units: [" 12-A ", "12-a"] })).live_units, ["12-A"]);
});

test("each mode says what it does, and live outside production says it still captures", () => {
  assert.match(describeMode("off", { is_production: false }), /skipped/);
  assert.match(describeMode("capture", { is_production: true }), /sends nothing/);
  assert.match(describeMode("live", { is_production: false }), /Here, only test sends go out/);
  assert.match(describeMode("live", { is_production: true }), /Other units' messages are captured/);
});

test("only a save that starts real sends in production asks for a second look", () => {
  const live = (patch: Partial<BookingSettings> = {}) => settings({ send_mode: "live", ...patch });
  assert.ok(startsLiveSends(settings(), live({ live_units: ["320-03"] }), true));
  assert.ok(startsLiveSends(live({ live_units: ["320-03"] }), live({ live_units: ["320-03", "12-A"] }), true));
  assert.ok(startsLiveSends(live(), live({ test_sends_enabled: true }), true));
  assert.ok(!startsLiveSends(settings(), live({ live_units: ["320-03"] }), false));
  assert.ok(!startsLiveSends(live({ live_units: ["320-03"] }), live({ live_units: ["320-03"] }), true));
  assert.ok(!startsLiveSends(live({ live_units: ["320-03", "12-A"] }), live({ live_units: ["12-a"] }), true));
  assert.ok(!startsLiveSends(settings(), live(), true));
  assert.ok(!startsLiveSends(live({ live_units: ["320-03"] }), settings({ send_mode: "off" }), true));
});

test("a run of the sending step is summed up in one line", () => {
  assert.equal(describeDrain(report()), "Nothing was due.");
  assert.equal(describeDrain(report({ captured: 2, sent: 1 })), "1 sent, 2 captured.");
  assert.equal(
    describeDrain(report({ failed: 1, retrying: 1, interrupted: 1 })),
    "1 failed, 1 to try again later, 1 cut off midway, now unknown.",
  );
  assert.equal(
    describeDrain(report({ released: 2, stopped: 1, captured: 2 })),
    "2 released from hold, 1 stopped, 2 captured.",
  );
});

test("held and cancelled messages have labels", () => {
  assert.equal(STATUS_LABELS.held, "Held");
  assert.equal(STATUS_LABELS.cancelled, "Cancelled");
});

test("the guard's reasons read as words, never claim an outcome, and unknown ones pass through", () => {
  assert.equal(describeGuard("not_production"), "Outside production");
  assert.equal(describeGuard("live_unit"), "Live unit in production");
  assert.equal(describeGuard("something_new"), "something_new");
  assert.equal(describeGuard(null), "");
});

test("kinds read as words, times read in Eastern time", () => {
  assert.equal(humanize("prospect_confirmation"), "Prospect confirmation");
  assert.equal(formatWhen("2027-04-14T20:00:00Z"), "Wed, Apr 14, 4:00 PM");
  assert.equal(formatWhen(null), "");
  assert.equal(formatWhen("not a time"), "not a time");
});

test("failed and unknown messages stand out", () => {
  assert.ok(needsAttention("failed") && needsAttention("unknown"));
  assert.ok(!needsAttention("captured") && !needsAttention("sent"));
});

test("the server's reason for a refusal is shown when it gives one", () => {
  assert.equal(refusalMessage(409, { detail: "Settings changed elsewhere." }, "Could not save"), "Settings changed elsewhere.");
  assert.equal(
    refusalMessage(422, { detail: [{ msg: "Value error, A live unit cannot be blank." }] }, "Could not save"),
    "A live unit cannot be blank.",
  );
  assert.equal(refusalMessage(500, null, "Could not save"), "Could not save (500).");
  assert.equal(refusalMessage(0, null, "Network error"), "Network error");
});

test("the callback's result is shown only when this tab knows it", () => {
  assert.deepEqual(connectResult("connected"), { code: "connected", ...CONNECT_RESULTS.connected });
  assert.equal(connectResult("missing_permissions")?.ok, false);
  assert.match(connectResult("missing_permissions")?.message ?? "", /both calendar permissions/);
  assert.equal(connectResult("toString"), null);
  assert.equal(connectResult("something_else"), null);
  assert.equal(connectResult(null), null);
  // Every result the API can send has words here (connections.py's ConnectResult).
  for (const code of ["connected", "denied", "missing_permissions", "expired", "invalid", "not_configured", "failed"]) {
    assert.ok(connectResult(code), code);
  }
});

test("the last read says whether it worked, and when", () => {
  assert.equal(describeLastRead({ last_read_at: null, last_read_ok: null }), "Not read yet.");
  assert.equal(describeLastRead({ last_read_at: "2026-11-15T21:32:00Z", last_read_ok: true }), "Last read Sun, Nov 15, 4:32 PM: fine.");
  assert.match(describeLastRead({ last_read_at: "2026-11-15T21:32:00Z", last_read_ok: false }), /a problem\.$/);
});

test("a connection offers only the buttons that can work here", () => {
  assert.deepEqual(connectionActions({ status: "connected", usable_here: true }, true), {
    check: true,
    reconnect: true,
    disconnect: true,
  });
  assert.deepEqual(connectionActions({ status: "connected", usable_here: false }, false), {
    check: false,
    reconnect: false,
    disconnect: true,
  });
  assert.deepEqual(connectionActions({ status: "lost", usable_here: false }, true), {
    check: false,
    reconnect: true,
    disconnect: true,
  });
  assert.deepEqual(connectionActions({ status: "revoked", usable_here: false }, true), {
    check: false,
    reconnect: true,
    disconnect: false,
  });
});

test("the lead agent's switch is part of the settings and compares in any key order", () => {
  const reordered = { lead_agent_uses_open_times: true, test_sends_enabled: false, live_units: [], send_mode: "capture" } as const;
  assert.ok(sameSettings(settings({ lead_agent_uses_open_times: true }), { ...reordered, live_units: [] }));
  assert.ok(!sameSettings(settings(), settings({ lead_agent_uses_open_times: true })));
});

test("calendar addresses are read from lines or commas, each once", () => {
  assert.deepEqual(parseCalendars(" jane@example.com,\nJANE@example.com ; team@example.com "), [
    "jane@example.com",
    "team@example.com",
  ]);
  assert.deepEqual(parseCalendars(""), []);
});

test("an agent is sent trimmed, with an empty mobile as none and short times", () => {
  const draft = {
    ...newAgent(),
    name: " Jane Doe ",
    email: " jane@example.com",
    mobile: "  ",
    busy_calendar_ids: ["a@example.com", "A@example.com"],
    weekly_hours: [{ days: ["mon" as const], start: "16:00:00", end: "18:30:00" }],
  };
  const clean = cleanAgent(draft);
  assert.equal(clean.name, "Jane Doe");
  assert.equal(clean.email, "jane@example.com");
  assert.equal(clean.mobile, null);
  assert.deepEqual(clean.busy_calendar_ids, ["a@example.com"]);
  assert.deepEqual(clean.weekly_hours, [{ days: ["mon"], start: "16:00", end: "18:30" }]);
});

test("windows and offers read the way the team says them", () => {
  assert.equal(clock("16:00:00"), "4:00 PM");
  assert.equal(clock("00:30"), "12:30 AM");
  assert.equal(clock("12:00"), "12:00 PM");
  assert.equal(
    describeWindows([{ days: ["mon", "wed"], start: "16:00", end: "18:30" }], "none"),
    "Mon/Wed 4:00 PM–6:30 PM",
  );
  assert.equal(describeWindows([], "Any time"), "Any time");
  assert.equal(describeOffer({ kind: "join", preferred: true, places_left: 1 }), "Join, 1 place left");
  assert.equal(describeOffer({ kind: "join", preferred: false, places_left: 2 }), "Join, 2 places left");
  assert.equal(describeOffer({ kind: "new", preferred: true, places_left: 4 }), "Preferred");
  assert.equal(describeOffer({ kind: "new", preferred: false, places_left: 4 }), "Open");
});

test("a failed check's reasons are kept, and its message is shown", () => {
  const body = { detail: { message: "Not saved.", problems: ["priority: must be from 1 to 99"] } };
  assert.deepEqual(refusalProblems(body), ["priority: must be from 1 to 99"]);
  assert.equal(refusalMessage(422, body, "Could not save"), "Not saved.");
  assert.deepEqual(refusalProblems({ detail: "Nope" }), []);
  assert.deepEqual(refusalProblems(null), []);
});

const PEOPLE = [
  { id: 1, name: "Alex Parker", mobile: "412-555-0117", email: "alex@example.com", source: "page" as const, texts_accepted: true, attention_reason: null },
  { id: 2, name: "Robin Lee", mobile: "no mobile", email: null, source: "team" as const, texts_accepted: false, attention_reason: null },
];
const SHOWING = { unit: "320-03", starts_at: "2027-04-14T20:00:00Z", host_name: "Jane Doe", people: PEOPLE };

test("a team cancellation says what it changes and sends", () => {
  const all = describeShowingCancel(SHOWING, "live");
  assert.match(all, /^Cancel the showing of 320-03 on .* for the 2 people booked\?/);
  assert.match(all, /1 of them get a cancellation text with a link to the units open for showings/);
  assert.match(all, /Jane Doe's event is cancelled\.$/);
  assert.match(describeShowingCancel({ ...SHOWING, people: [PEOPLE[0]] }, "live"), /the 1 person booked\? Each gets/);
  assert.match(describeShowingCancel(SHOWING, "capture"), /Capture mode: these are kept under Messages, not sent\.$/);
  assert.match(describeShowingCancel(SHOWING, "off"), /none of these go out\.$/);

  const one = describeRemoval(SHOWING, PEOPLE[0], "live");
  assert.match(one, /^Remove Alex Parker from the showing of 320-03/);
  assert.match(one, /Alex Parker gets a cancellation text, their calendar invite is withdrawn, and Jane Doe's event is updated\.$/);
  const last = describeRemoval({ ...SHOWING, people: [PEOPLE[1]] }, PEOPLE[1], "live");
  assert.match(last, /Robin Lee gets no text, .*the showing is cancelled, with Jane Doe's event\.$/);
});

test("a booking that needs the team says why", () => {
  assert.equal(describeAttention("confirmation_undelivered"), "Confirmation text not delivered: call them");
  assert.equal(describeAttention("something_new"), "Something new: call them");
});

test("a number known to be wrong is not counted as texted", () => {
  const wrong = { ...PEOPLE[0], attention_reason: "confirmation_undelivered" };
  assert.match(describeRemoval({ ...SHOWING, people: [wrong] }, wrong, "live"), /Alex Parker gets no text, /);
  assert.match(describeShowingCancel({ ...SHOWING, people: [wrong] }, "live"), /0 of them get a cancellation text/);
});
