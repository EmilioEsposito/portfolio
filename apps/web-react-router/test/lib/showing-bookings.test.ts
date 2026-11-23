import assert from "node:assert/strict";
import test from "node:test";

import {
  type BookingSettings,
  type CapturedNotice,
  type DrainReport,
  type SendResult,
  type SettingsView,
  CONNECT_RESULTS,
  MODE_LABELS,
  SEND_MODES,
  STATUS_LABELS,
  afterSaving,
  capturedLine,
  cleanSettings,
  connectResult,
  connectionActions,
  describeLastRead,
  describeDrain,
  describeGuard,
  describeSendResult,
  formatWhen,
  heldBackNote,
  humanize,
  isTicked,
  joinWords,
  liveStartQuestion,
  needsAttention,
  describeNeedingTeam,
  noticesNeedingTeam,
  type Notice,
  pickedNotices,
  cleanAgent,
  clock,
  describeAttention,
  leadAgentLine,
  describeOffer,
  describeRemoval,
  describeResidentAttention,
  describeShowingCancel,
  describeWindows,
  eachOnce,
  newAgent,
  noticeLeadConversation,
  noticeRecipients,
  refusalMessage,
  refusalProblems,
  rightNow,
  sameSettings,
  sendQuestion,
  summarizeSend,
  tickUnit,
  unitTicks,
  untickedNote,
} from "../../app/sites/team/lib/showing-bookings.ts";

const settings = (patch: Partial<BookingSettings> = {}): BookingSettings => ({
  send_mode: "capture",
  live_units: [],
  live_all_units: false,
  lead_agent_uses_open_times: false,
  ...patch,
});

const LISTINGS = [
  { unit: "320-03", address: "320 Example St" },
  { unit: "320-05", address: "320 Example St" },
  { unit: "412-01", address: "412 Example St" },
];

const view = (patch: Partial<BookingSettings> = {}, over: Partial<SettingsView> = {}): SettingsView => ({
  settings: settings(patch),
  environment: "production",
  is_production: true,
  test_recipients: ["•••-0150"],
  public_booking_on: true,
  listings: LISTINGS,
  ...over,
});
const PREVIEW = { environment: "portfolio-pr-1", is_production: false };

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

test("units are ticked and unticked in any case, and the others stay", () => {
  assert.deepEqual(tickUnit(["320-03"], "12-A", true), ["320-03", "12-A"]);
  assert.deepEqual(tickUnit(["320-03", "12-a"], "12-A", true), ["320-03", "12-A"]);
  assert.deepEqual(tickUnit(["320-03", "12-a"], "12-A", false), ["320-03"]);
  assert.ok(isTicked(["12-a"], "12-A") && !isTicked(["12-a"], "12-B"));
});

test("every rental listing can be ticked, and a ticked unit whose listing is gone stays to be unticked", () => {
  assert.deepEqual(
    unitTicks(LISTINGS, ["320-05", "404-Z"], ["412-01"]).map((each) => [each.unit, each.listed]),
    [["320-03", true], ["320-05", true], ["412-01", true], ["404-Z", false]],
  );
  // Unticked and not saved yet: still shown, so the edit can be seen.
  assert.deepEqual(unitTicks([], ["404-Z"], []).map((each) => each.unit), ["404-Z"]);
  assert.deepEqual(unitTicks([], [], []), []);
});

test("two settings that differ only in the order or spelling of ticks are the same", () => {
  assert.ok(sameSettings(settings({ live_units: ["320-03", "12-A"] }), settings({ live_units: ["12-A", "320-03"] })));
  assert.ok(sameSettings(settings({ live_units: ["320-03", " 320-03"] }), settings({ live_units: ["320-03"] })));
  assert.ok(!sameSettings(settings(), settings({ send_mode: "live" })));
  assert.ok(!sameSettings(settings(), settings({ live_all_units: true })));
  assert.ok(!sameSettings(settings(), settings({ live_units: ["320-03"] })));
  assert.deepEqual(cleanSettings(settings({ live_units: [" 12-A ", "12-a"] })).live_units, ["12-A"]);
});

test("the modes are offered in the order test, capture, live", () => {
  assert.deepEqual([...SEND_MODES], ["test", "capture", "live"]);
  // A message from when the mode could be off still has a label.
  assert.equal(MODE_LABELS.off, "Off");
});

test("words are joined as they are said", () => {
  assert.equal(joinWords([]), "");
  assert.equal(joinWords(["320-03"]), "320-03");
  assert.equal(joinWords(["320-03", "412-01"]), "320-03 and 412-01");
  assert.equal(joinWords(["320-03", "320-05", "412-01"]), "320-03, 320-05 and 412-01");
});

test("right now says who can book and what is sent, from the saved settings", () => {
  const says = (patch: Partial<BookingSettings>, over: Partial<SettingsView> = {}, pagesOn = true) =>
    rightNow(view(patch, over), pagesOn);
  assert.equal(
    says({}),
    "Your team and the public can book. Nothing is sent until you send it from Captured messages.",
  );
  assert.equal(
    says({ lead_agent_uses_open_times: true }),
    "Your team, the public and Zillow leads can book. Nothing is sent until you send it from Captured messages.",
  );
  assert.match(says({ lead_agent_uses_open_times: true }, { public_booking_on: false }), /^Your team and Zillow leads can book\./);
  assert.match(says({}, { public_booking_on: false }), /^Your team can book\./);
  assert.equal(
    says({ send_mode: "test" }),
    "Your team and the public can book. Only the test list is texted. Everything else is captured.",
  );
  assert.match(says({ send_mode: "test" }, { test_recipients: [] }), /No test list is set here, so nobody is texted\. Everything is captured\.$/);
  assert.match(says({ send_mode: "live", live_units: ["320-03"] }), /Real people are texted for 320-03 only\.$/);
  assert.match(says({ send_mode: "live", live_units: ["320-03", "412-01"] }), /texted for 320-03 and 412-01 only\.$/);
  assert.match(says({ send_mode: "live", live_all_units: true, live_units: ["320-03"] }), /Real people are texted for every unit\.$/);
});

test("with no unit ticked, live closes every unit, so only the team can book", () => {
  assert.equal(
    rightNow(view({ send_mode: "live", lead_agent_uses_open_times: true }), true),
    "Your team can book. No unit is ticked, so nobody is texted.",
  );
});

test("while the public pages answer 404 the public is not said to book", () => {
  assert.match(rightNow(view(), false), /^Your team can book\./);
  assert.match(rightNow(view({ lead_agent_uses_open_times: true }), false), /^Your team and Zillow leads can book\./);
});

test("outside production right now never says real people are texted", () => {
  for (const patch of [{ live_units: ["320-03"] }, { live_all_units: true }, {}]) {
    const line = rightNow(view({ send_mode: "live", ...patch }, PREVIEW), true);
    assert.match(line, /This is not production, so nobody is texted\. Everything is captured\.$/);
  }
  assert.match(rightNow(view({ send_mode: "test" }, PREVIEW), true), /Only the test list is texted\./);
});

test("unsaved edits are said as they would be, keeping the saved switches", () => {
  const saved = view({ lead_agent_uses_open_times: true });
  const form = settings({ send_mode: "live", live_units: ["320-03"], lead_agent_uses_open_times: true });
  assert.equal(
    afterSaving(saved, form, true),
    "Your team, the public and Zillow leads can book. Real people are texted for 320-03 only.",
  );
  assert.equal(rightNow(saved, true), rightNow(view({ lead_agent_uses_open_times: true }), true));
});

test("only a save that starts real sends in production asks, and names the units", () => {
  const live = (patch: Partial<BookingSettings> = {}) => settings({ send_mode: "live", ...patch });
  assert.equal(
    liveStartQuestion(settings(), live({ live_units: ["320-03", "412-01"] }), true),
    "This starts real texts and calendar invites for 320-03 and 412-01. Save?",
  );
  // Only the ones it starts.
  assert.equal(
    liveStartQuestion(live({ live_units: ["320-03"] }), live({ live_units: ["320-03", "12-A"] }), true),
    "This starts real texts and calendar invites for 12-A. Save?",
  );
  // A tick saved while the mode was another one starts now.
  assert.match(
    liveStartQuestion(settings({ live_units: ["320-03"] }), live({ live_units: ["320-03"] }), true) ?? "",
    /for 320-03\. Save\?$/,
  );
  assert.match(
    liveStartQuestion(settings({ send_mode: "test" }), live({ live_all_units: true }), true) ?? "",
    /for every unit, including ones added later\. Save\?$/,
  );
  assert.match(liveStartQuestion(live({ live_units: ["320-03"] }), live({ live_all_units: true }), true) ?? "", /every unit/);
  const none: [BookingSettings, BookingSettings, boolean][] = [
    [settings(), live({ live_units: ["320-03"] }), false],
    [live({ live_units: ["320-03"] }), live({ live_units: ["320-03"] }), true],
    [live({ live_units: ["320-03", "12-A"] }), live({ live_units: ["12-a"] }), true],
    [live({ live_all_units: true }), live({ live_units: ["320-03"] }), true],
    [live({ live_all_units: true }), live({ live_all_units: true, live_units: ["320-03"] }), true],
    [settings(), live(), true],
    [settings(), settings({ send_mode: "test" }), true],
    [live({ live_units: ["320-03"] }), settings(), true],
  ];
  for (const [saved, next, production] of none) assert.equal(liveStartQuestion(saved, next, production), null);
});

test("unticking a live unit says what happens to the showings booked there", () => {
  const live = (patch: Partial<BookingSettings> = {}) => settings({ send_mode: "live", ...patch });
  assert.equal(
    untickedNote(live({ live_units: ["320-03", "320-05"] }), live({ live_units: ["320-03"] }), LISTINGS),
    "Showings already booked at 320-05 stay, and their messages are captured.",
  );
  assert.equal(
    untickedNote(live({ live_all_units: true }), live({ live_units: ["320-03"] }), LISTINGS),
    "Showings already booked at 320-05 and 412-01 stay, and their messages are captured.",
  );
  assert.equal(untickedNote(live({ live_units: ["320-03"] }), live({ live_all_units: true }), LISTINGS), null);
  assert.equal(untickedNote(live({ live_units: ["320-03"] }), live({ live_units: ["320-03", "412-01"] }), LISTINGS), null);
  assert.equal(untickedNote(live({ live_units: ["320-03"] }), settings(), LISTINGS), null);
  assert.equal(untickedNote(settings({ live_units: ["320-03"] }), live(), LISTINGS), null);
});

test("a confirmation says what is not really sent, by mode and unit", () => {
  assert.equal(heldBackNote(settings(), true, "320-03"), " Capture mode: these are kept under Captured messages, not sent.");
  assert.match(heldBackNote(settings({ send_mode: "test" }), true, "320-03"), /only messages to the test list are sent/);
  const live = settings({ send_mode: "live", live_units: ["320-03"] });
  assert.equal(heldBackNote(live, true, "320-03"), "");
  assert.equal(heldBackNote(live, true, "412-01"), " 412-01 is not ticked for Live mode: these are captured, not sent.");
  assert.equal(heldBackNote(settings({ send_mode: "live", live_all_units: true }), true, "412-01"), "");
  assert.match(heldBackNote(live, false, "320-03"), /Outside production these are captured/);
});

const captured = (patch: Partial<CapturedNotice> = {}): CapturedNotice => ({
  id: 1,
  kind: "prospect_confirmation",
  kind_label: "Prospect's confirmation",
  channel: "sms",
  unit: "320-03",
  showing_starts_at: "2027-04-14T20:00:00Z",
  to: "Alex Parker, 412-555-0117",
  wording: "Sernia Capital: your in-person showing is booked.",
  conversation_id: null,
  state: "ready",
  reason: null,
  note: null,
  waits_for_notice_id: null,
  confirmation_on_its_way: false,
  goes_out: true,
  captured_at: "2027-04-13T18:00:00Z",
  ...patch,
});

test("ready messages are ticked until someone unticks them, and no other can be", () => {
  const list = [
    captured({ id: 1 }),
    captured({ id: 2 }),
    captured({ id: 3, state: "too_late" }),
    captured({ id: 4, state: "no_longer_applies" }),
  ];
  assert.deepEqual(pickedNotices(list, new Set()).map((each) => each.id), [1, 2]);
  assert.deepEqual(pickedNotices(list, new Set([2, 3])).map((each) => each.id), [1]);
});

test("a captured message says why it is grayed, or why it would wait", () => {
  const none = new Set<number>();
  assert.equal(capturedLine(captured(), none), null);
  assert.equal(
    capturedLine(captured({ state: "no_longer_applies", reason: "The booking was cancelled." }), none),
    "The booking was cancelled.",
  );
  // It waits for its confirmation only while both are ticked, or while that is on its way.
  const invite = captured({ id: 2, kind: "prospect_invite", waits_for_notice_id: 1 });
  assert.equal(capturedLine(invite, new Set([1, 2])), "Goes once the confirmation is delivered.");
  assert.equal(capturedLine(invite, new Set([2])), null);
  assert.equal(
    capturedLine(captured({ confirmation_on_its_way: true }), none),
    "Goes once the confirmation is delivered.",
  );
  const residents = captured({ note: "Residents get texts only between 8 AM and 9 PM.", waits_for_notice_id: 1 });
  assert.equal(capturedLine(residents, none), "Residents get texts only between 8 AM and 9 PM.");
  assert.equal(
    capturedLine(residents, new Set([1])),
    "Residents get texts only between 8 AM and 9 PM. Goes once the confirmation is delivered.",
  );
});

test("the question before sending says how many go to real people", () => {
  assert.equal(sendQuestion([captured()], true), "1 message goes to a real person. Once sent, it can't be taken back.");
  assert.match(sendQuestion([captured(), captured({ id: 2 })], true), /^2 messages go to real people\./);
  const kept = captured({ id: 2, goes_out: false });
  const none = "No message goes to real people: outside production only the test list can be reached.";
  assert.equal(sendQuestion([captured()], false), `${none} 1 message goes to the test list.`);
  assert.equal(sendQuestion([captured(), kept], false), `${none} 1 message goes to the test list. 1 message stays captured.`);
  assert.equal(sendQuestion([kept], false), `${none} It stays captured.`);
  assert.equal(sendQuestion([kept, { ...kept, id: 3 }], false), `${none} They stay captured.`);
});

const sent = (patch: Partial<SendResult> = {}): SendResult => ({
  id: 1,
  kind_label: "Prospect's confirmation",
  unit: "320-03",
  showing_starts_at: "2027-04-14T20:00:00Z",
  to: "Alex Parker, 412-555-0117",
  outcome: "sent",
  detail: "",
  waits_until: null,
  ...patch,
});

test("what happened to each message is said plainly", () => {
  assert.equal(describeSendResult(sent()), "Sent.");
  assert.equal(
    describeSendResult(sent({ outcome: "waiting", detail: "Goes once the confirmation is delivered." })),
    "Waiting. Goes once the confirmation is delivered.",
  );
  assert.equal(
    describeSendResult(
      sent({
        outcome: "waiting",
        detail: "Residents get texts only between 8 AM and 9 PM.",
        waits_until: "2027-04-14T12:00:00Z",
      }),
    ),
    "Waiting until Wed, Apr 14, 8:00 AM. Residents get texts only between 8 AM and 9 PM.",
  );
  assert.equal(
    describeSendResult(sent({ outcome: "not_sent", detail: "The booking was cancelled." })),
    "Not sent. The booking was cancelled.",
  );
  assert.equal(
    summarizeSend([sent(), sent({ id: 2 }), sent({ id: 3, outcome: "waiting" }), sent({ id: 4, outcome: "not_sent" })]),
    "2 sent, 1 waiting, 1 not sent.",
  );
  assert.equal(summarizeSend([sent({ outcome: "kept" }), sent({ outcome: "handled" })]), "1 still captured, 1 already handled.");
  assert.equal(summarizeSend([]), "Nothing was sent.");
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
  assert.equal(describeGuard("approved"), "Sent by a person");
  assert.equal(describeGuard("not_test_recipient"), "Not on the test list");
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

test("failed or stuck messages are counted for the team", () => {
  const notice = (attention: Notice["attention"]) => ({ attention }) as Notice;
  assert.equal(describeNeedingTeam([notice(null)]), null);
  assert.equal(describeNeedingTeam([notice("failed"), notice(null)]), "1 message needs a look.");
  assert.equal(describeNeedingTeam([notice("failed"), notice("stuck")]), "2 messages need a look.");
  assert.equal(noticesNeedingTeam([notice("stuck"), notice(null)]).length, 1);
});

test("a run that queued reminders and flagged messages says so", () => {
  assert.equal(
    describeDrain(report({ prospect_reminders: 2, resident_reminders: 1, sent: 3, alerts: 1 })),
    "3 reminders queued, 3 sent, 1 flagged to the team.",
  );
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
  assert.match(connectResult("missing_permissions")?.message ?? "", /free\/busy and Sernia showings calendar permissions are both needed/);
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
  const reordered = { lead_agent_uses_open_times: true, live_all_units: false, live_units: [], send_mode: "capture" } as const;
  assert.ok(sameSettings(settings({ lead_agent_uses_open_times: true }), { ...reordered, live_units: [] }));
  assert.ok(!sameSettings(settings(), settings({ lead_agent_uses_open_times: true })));
});

test("calendar addresses are kept as first spelled, each once in any case", () => {
  assert.deepEqual(eachOnce(["Jane@example.com", "team@example.com", "jane@EXAMPLE.com", ""]), [
    "Jane@example.com",
    "team@example.com",
  ]);
  // Never split: an address with a comma in it is one calendar.
  assert.deepEqual(eachOnce(["odd,one@example.com"]), ["odd,one@example.com"]);
  assert.deepEqual(eachOnce([]), []);
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
  { id: 1, name: "Alex Parker", mobile: "412-555-0117", email: "alex@example.com", source: "page" as const, texts_accepted: true, attention_reason: null, attendance: "unknown" as const },
  { id: 2, name: "Robin Lee", mobile: "no mobile", email: null, source: "team" as const, texts_accepted: false, attention_reason: null, attendance: "unknown" as const },
];
const SHOWING = { unit: "320-03", starts_at: "2027-04-14T20:00:00Z", host_name: "Jane Doe", people: PEOPLE };

test("a team cancellation says what it changes and sends", () => {
  const all = describeShowingCancel(SHOWING, "");
  assert.match(all, /^Cancel the showing of 320-03 on .* for the 2 people booked\?/);
  assert.match(all, /1 of them get a cancellation text with a link to the units open for showings/);
  assert.match(all, /Jane Doe's event is cancelled\.$/);
  assert.match(describeShowingCancel({ ...SHOWING, people: [PEOPLE[0]] }, ""), /the 1 person booked\? Each gets/);
  const note = heldBackNote(settings(), true, "320-03");
  assert.match(describeShowingCancel(SHOWING, note), /Capture mode: these are kept under Captured messages, not sent\.$/);
  assert.match(describeRemoval(SHOWING, PEOPLE[0], note), /Captured messages, not sent\.$/);

  const one = describeRemoval(SHOWING, PEOPLE[0], "");
  assert.match(one, /^Remove Alex Parker from the showing of 320-03/);
  assert.match(one, /Alex Parker gets a cancellation text, their calendar invite is withdrawn, and Jane Doe's event is updated\.$/);
  const last = describeRemoval({ ...SHOWING, people: [PEOPLE[1]] }, PEOPLE[1], "");
  assert.match(last, /Robin Lee gets no text, .*the showing is cancelled, with Jane Doe's event\.$/);
});

test("a booking that needs the team says why", () => {
  assert.equal(describeAttention("confirmation_undelivered"), "Confirmation text not delivered: call them");
  assert.equal(describeAttention("something_new"), "Something new: call them");
});

test("a showing whose residents were not texted says why", () => {
  assert.equal(describeResidentAttention("lookup_failed"), "Could not read who lives here: tell the resident yourself");
  assert.equal(
    describeResidentAttention("lease_dates_missing"),
    "A contact at this unit has no lease dates: check who lives here and tell them yourself",
  );
  assert.equal(describeResidentAttention("something_new"), "Residents not texted (Something new): tell them yourself");
});

test("a group text names every number it reaches", () => {
  const one = { recipient: "+14125550142", payload: { body: "Hi" } };
  assert.equal(noticeRecipients(one), "+14125550142");
  const group = { recipient: "+14125550142", payload: { body: "Hi", also_to: ["+14125550143"] } };
  assert.equal(noticeRecipients(group), "+14125550142, +14125550143");
});

test("an email in a lead's Zillow thread links to its conversation, and nothing else does", () => {
  const email = { channel: "email" as const, payload: { subject: "Re: hi", body: "Hi", conversation_id: "conv-1" } };
  assert.equal(noticeLeadConversation(email), "conv-1");
  assert.equal(noticeLeadConversation({ channel: "email", payload: { subject: "Hi", body: "Hi" } }), null);
  assert.equal(noticeLeadConversation({ channel: "sms", payload: { body: "Hi", conversation_id: "conv-1" } }), null);
});

test("reminders by email count among the reminders queued", () => {
  const report = { captured: 0, sent: 0, failed: 0, unknown: 0, skipped: 0, retrying: 0, interrupted: 0 };
  assert.equal(describeDrain({ ...report, prospect_reminders: 1, lead_email_reminders: 1 }), "2 reminders queued.");
});

test("a number known to be wrong is not counted as texted", () => {
  const wrong = { ...PEOPLE[0], attention_reason: "confirmation_undelivered" };
  assert.match(describeRemoval({ ...SHOWING, people: [wrong] }, wrong, ""), /Alex Parker gets no text, /);
  assert.match(describeShowingCancel({ ...SHOWING, people: [wrong] }, ""), /0 of them get a cancellation text/);
});

test("a place booked by email says so, and whether the details arrived", () => {
  assert.equal(
    leadAgentLine({ source: "lead_agent", details_pending: true }),
    "Booked by email with the lead agent · details not added yet",
  );
  assert.equal(leadAgentLine({ source: "lead_agent", details_pending: false }), "Booked by email with the lead agent · details added");
  assert.equal(leadAgentLine({ source: "page", details_pending: false }), null);
  assert.equal(
    describeAttention("reply_uncertain"),
    "The email reply that booked this may not have gone out: check the thread",
  );
});
