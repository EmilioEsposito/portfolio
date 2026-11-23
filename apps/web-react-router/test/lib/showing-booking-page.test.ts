import assert from "node:assert/strict";
import test from "node:test";

import {
  type FormValues,
  type Offer,
  SERVER_MESSAGE,
  UNIT_GONE_MESSAGE,
  bestDayKey,
  bookingBody,
  bookingPlace,
  cancelInTheTextLine,
  dayKey,
  fieldOrder,
  findOffer,
  firstMarked,
  fixedLine,
  formatDay,
  formatSpan,
  formatTime,
  formatWhen,
  groupOffers,
  leadTokenOf,
  missingFields,
  readRefusal,
  refusalAction,
  refusalMessage,
  sameLinkLine,
  textedLine,
  ticksMessage,
  unitSummary,
} from "../../app/sites/sernia-public/lib/showing-booking-page.ts";

const NEW_YORK = "America/New_York";
const CONFIRMATIONS = [{ key: "lease_start" }, { key: "pets" }, { key: "texts" }];

const offer = (start: string, patch: Partial<Offer> = {}): Offer => ({
  start,
  join: false,
  preferred: false,
  places_left: 4,
  ...patch,
});

test("times are worded in the unit's time zone, not the machine's", () => {
  // 20:30 UTC on Wed 14 Apr 2027 is 4:30 PM in New York (daylight time).
  const start = "2027-04-14T20:30:00Z";
  assert.equal(formatDay(start, NEW_YORK), "Wed, Apr 14");
  assert.equal(formatTime(start, NEW_YORK), "4:30 PM");
  assert.equal(formatWhen(start, NEW_YORK), "Wed, Apr 14 at 4:30 PM");
  assert.equal(formatWhen(start, "America/Los_Angeles"), "Wed, Apr 14 at 1:30 PM");
  // An offset in the string is the same instant.
  assert.equal(formatWhen("2027-04-14T16:30:00-04:00", NEW_YORK), "Wed, Apr 14 at 4:30 PM");
  // Plain spaces, whatever the ICU version prefers before AM/PM.
  assert.doesNotMatch(formatTime(start, NEW_YORK), / /);
});

test("a time late in the evening in New York stays on its own day", () => {
  // 03:30 UTC on Thu 15 Apr is still Wed 14 Apr in New York.
  const late = "2027-04-15T03:30:00Z";
  assert.equal(dayKey(late, NEW_YORK), "2027-04-14");
  assert.equal(dayKey(late, "UTC"), "2027-04-15");
  assert.equal(formatWhen(late, NEW_YORK), "Wed, Apr 14 at 11:30 PM");
});

test("winter times use standard time", () => {
  assert.equal(formatWhen("2027-01-13T21:00:00Z", NEW_YORK), "Wed, Jan 13 at 4:00 PM");
  assert.equal(formatTime("2027-01-13T17:00:00Z", NEW_YORK), "12:00 PM");
});

test("a showing's span shares AM/PM when it can", () => {
  assert.equal(formatSpan("2027-04-14T20:00:00Z", "2027-04-14T20:30:00Z", NEW_YORK), "Wed, Apr 14, 4:00 to 4:30 PM");
  assert.equal(formatSpan("2027-04-14T15:45:00Z", "2027-04-14T16:15:00Z", NEW_YORK), "Wed, Apr 14, 11:45 AM to 12:15 PM");
});

test("offers are grouped by day and ordered by time, joins and preferred times both suggested", () => {
  // The API orders joins first, then preferred times, then the rest.
  const offers = [
    offer("2027-04-15T20:00:00Z", { join: true, places_left: 3 }),
    offer("2027-04-14T21:00:00Z", { preferred: true }),
    offer("2027-04-14T20:00:00Z"),
    offer("2027-04-15T03:30:00Z"),
    offer("2027-04-17T14:00:00Z", { join: true, preferred: true }),
  ];
  const days = groupOffers(offers, NEW_YORK);
  assert.deepEqual(
    days.map((day) => [day.key, day.label, day.times.map((time) => [time.label, time.note])]),
    [
      ["2027-04-14", "Wed, Apr 14", [["4:00 PM", null], ["5:00 PM", "Suggested"], ["11:30 PM", null]]],
      ["2027-04-15", "Thu, Apr 15", [["4:00 PM", "Suggested"]]],
      ["2027-04-17", "Sat, Apr 17", [["10:00 AM", "Suggested"]]],
    ],
  );
  assert.deepEqual(groupOffers([], NEW_YORK), []);
});

test("the picker opens on the day of the API's first offer, not the earliest day", () => {
  // The API ranks a join on Thursday first; Wednesday has earlier times.
  const offers = [
    offer("2027-04-15T20:00:00Z", { join: true }),
    offer("2027-04-14T21:00:00Z", { preferred: true }),
    offer("2027-04-14T20:00:00Z"),
  ];
  assert.equal(bestDayKey(offers, NEW_YORK), "2027-04-15");
  // In the unit's time zone: 03:30 UTC Thursday is still Wednesday in New York.
  assert.equal(bestDayKey([offer("2027-04-15T03:30:00Z")], NEW_YORK), "2027-04-14");
  assert.equal(bestDayKey([], NEW_YORK), null);
});

test("an offer is found by its instant, whatever offset the start was written with", () => {
  const offers = [offer("2027-04-14T16:00:00-04:00")];
  assert.equal(findOffer(offers, "2027-04-14T20:00:00Z"), offers[0]);
  assert.equal(findOffer(offers, "2027-04-14T20:30:00Z"), undefined);
  assert.equal(findOffer(offers, null), undefined);
});

test("the unit's line and the booked place", () => {
  assert.equal(
    unitSummary({ unit: "320-03", address: "320 Example St", rent: 1450, showing_minutes: 30 }),
    "320 Example St, unit 320-03 · $1,450 a month · 30 minutes",
  );
  assert.equal(unitSummary({ unit: "320-03", address: "", rent: null, showing_minutes: 30 }), "Unit 320-03 · 30 minutes");
  assert.equal(bookingPlace({ address: "320 Example St", unit: "320-03" }), "320 Example St, unit 320-03");
  assert.equal(bookingPlace({ address: "", unit: "320-03" }), "Unit 320-03");
});

const filled: FormValues = {
  start: "2027-04-14T20:00:00Z",
  name: "Alex Parker",
  mobile: "412-555-0117",
  email: "alex@example.com",
  ticks: { lease_start: true, pets: true, texts: true },
};

test("missing fields get the API's own lines, and the ticks the mockup's count", () => {
  assert.deepEqual(missingFields(filled, CONFIRMATIONS), {});
  const missing = missingFields({ ...filled, start: null, name: "  ", ticks: { texts: true } }, CONFIRMATIONS);
  assert.deepEqual(missing, {
    start: "Pick a time.",
    name: "Enter your name.",
    "confirmations.lease_start": "Tick this box to book online.",
    "confirmations.pets": "Tick this box to book online.",
  });
  assert.equal(ticksMessage(missing), "2 boxes still need a tick. They are marked above.");
  assert.equal(ticksMessage({ "confirmations.pets": "x" }), "One box still needs a tick. It is marked above.");
  assert.equal(ticksMessage({ name: "x" }), null);
  // Only the ticks the API asked for count.
  assert.deepEqual(missingFields({ ...filled, ticks: { pets: true, texts: true } }, [{ key: "pets" }, { key: "texts" }]), {});
});

test("focus goes to the first marked field in page order", () => {
  const order = fieldOrder(CONFIRMATIONS);
  assert.deepEqual(order, [
    "start",
    "name",
    "mobile",
    "email",
    "confirmations.lease_start",
    "confirmations.pets",
    "confirmations.texts",
    "confirmations",
    "turnstile",
  ]);
  assert.equal(firstMarked({ "confirmations.pets": "x", email: "y" }, order), "email");
  assert.equal(firstMarked({ turnstile: "x" }, order), "turnstile");
  assert.equal(firstMarked({ other: "x" }, order), "other");
  assert.equal(firstMarked({}, order), undefined);
});

test("the booking body sends each tick the API asked for, the version and the lead token", () => {
  const unit = { confirmations: [{ key: "pets", wording: "w", version: "v1" }, { key: "texts", wording: "w", version: "v1" }], confirmations_version: "abc" };
  assert.deepEqual(bookingBody({ ...filled, ticks: { pets: true, lease_start: true } }, unit, "token-1", "lead-9"), {
    start: "2027-04-14T20:00:00Z",
    name: "Alex Parker",
    mobile: "412-555-0117",
    email: "alex@example.com",
    confirmations: { pets: true, texts: false },
    confirmations_version: "abc",
    turnstile_token: "token-1",
    lead_token: "lead-9",
  });
  assert.equal("lead_token" in bookingBody(filled, unit, "token-1", null), false);
});

test("a refusal's fields and message are read from the API's detail", () => {
  const refusal = readRefusal(422, {
    detail: { code: "invalid", message: "Check the marked fields.", fields: { mobile: "Enter a ten-digit US mobile number.", bad: 3 } },
  });
  assert.deepEqual(refusal, {
    status: 422,
    code: "invalid",
    message: "Check the marked fields.",
    fields: { mobile: "Enter a ten-digit US mobile number." },
  });
  assert.equal(refusalMessage(refusal), "Check the marked fields above.");
  assert.equal(
    refusalMessage(readRefusal(422, { detail: { code: "invalid", message: "m", fields: { "confirmations.pets": "Tick this box to book online." } } })),
    "One box still needs a tick. It is marked above.",
  );
});

test("an answer that is not ours: the switch's bare 404, a proxy error, no body", () => {
  assert.deepEqual(readRefusal(404, { detail: "Not Found" }), {
    status: 404,
    code: "unknown_unit",
    message: UNIT_GONE_MESSAGE,
    fields: {},
  });
  assert.deepEqual(readRefusal(502, { error: "Proxy Error" }), { status: 502, code: "unknown", message: SERVER_MESSAGE, fields: {} });
  assert.deepEqual(readRefusal(500, null), { status: 500, code: "unknown", message: SERVER_MESSAGE, fields: {} });
});

test("each refusal code leads to what the page does next", () => {
  const cases: Record<string, string> = {
    invalid: "fix",
    turnstile_failed: "fix",
    number_refused: "fix",
    already_booked: "fix",
    limit_mobile: "fix",
    limit_email: "fix",
    time_gone: "pick_again",
    full: "pick_again",
    confirmations_changed: "tick_again",
    limit_ip: "call",
    limit_site: "call",
    unavailable: "call",
    unknown_unit: "call",
    unknown: "call",
    no_booking: "call",
    already_completed: "fix",
  };
  for (const [code, action] of Object.entries(cases)) assert.equal(refusalAction({ code }), action, code);
});

test("the API's own message is shown for everything but a plain field check", () => {
  const gone = readRefusal(409, {
    detail: { code: "time_gone", message: "That time is no longer open. Pick another time.", fields: { start: "That time is no longer open." } },
  });
  assert.equal(refusalMessage(gone), "That time is no longer open. Pick another time.");
  assert.equal(refusalMessage(readRefusal(404, { detail: { code: "unknown_unit", message: "This unit is not open for showings.", fields: {} } })), UNIT_GONE_MESSAGE);
  const refused = readRefusal(422, {
    detail: { code: "number_refused", message: "We couldn't text this number. Check it, or call us instead.", fields: { mobile: "We couldn't text this number." } },
  });
  assert.equal(refusalMessage(refused), "We couldn't text this number. Check it, or call us instead.");
  assert.deepEqual(refused.fields, { mobile: "We couldn't text this number." });
});

test("only a token-shaped ?t= value is sent to look up a showing booked by email", () => {
  assert.equal(leadTokenOf("AbCdEf0123456789_-xyZ1"), "AbCdEf0123456789_-xyZ1");
  for (const value of [null, "", "short", "has space in it 12345", "a".repeat(65), "<script>alert(1)</script>"]) {
    assert.equal(leadTokenOf(value), null, String(value));
  }
});

test("the page says a text was sent only once it was", () => {
  const sent = { texted: true, mobile: "•••-•••-••17" };
  const captured = { ...sent, texted: false };
  assert.equal(textedLine(sent), "We texted •••-•••-••17.");
  assert.equal(textedLine(captured), "We'll text •••-•••-••17.");
  assert.equal(fixedLine(sent), "Done. We texted the confirmation to •••-•••-••17.");
  assert.equal(fixedLine(captured), "Done. We'll text the confirmation to •••-•••-••17.");
  assert.equal(sameLinkLine(sent), "The text has the same link.");
  assert.equal(sameLinkLine(captured), "The text will have the same link.");
  assert.equal(cancelInTheTextLine(sent), "The text we sent you has a link to cancel this showing.");
  assert.equal(cancelInTheTextLine(captured), "The text we'll send you has a link to cancel this showing.");
});
