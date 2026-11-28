import assert from "node:assert/strict";
import test from "node:test";

import { type UnitOffers, heldBackNote } from "../../app/sites/team/lib/showing-bookings.ts";
import {
  type TeamBookingForm,
  attendanceAfter,
  bookingBody,
  describeAttendance,
  describeBookingResult,
  describeHostChange,
  describeQueued,
  describeTeamBooking,
  emptyBookingForm,
  hostChoices,
  missingFields,
  refusalFields,
  timeOptions,
} from "../../app/sites/team/lib/showing-team-actions.ts";

const START = "2027-04-14T20:00:00Z"; // Wed, Apr 14, 4:00 PM Eastern

const units: UnitOffers[] = [
  {
    unit: "320-03",
    address: "320 Example St",
    notice_hours: 24,
    why_none: null,
    offers: [
      { start: START, kind: "join", preferred: false, places_left: 2, host_id: 1, host_name: "Jane Doe", showing_id: 7 },
      {
        start: "2027-04-14T21:00:00Z",
        kind: "new",
        preferred: true,
        places_left: 4,
        host_id: 1,
        host_name: "Jane Doe",
        showing_id: null,
      },
    ],
  },
  { unit: "412-01", address: "9 Other Rd", notice_hours: 24, why_none: "No open time.", offers: [] },
];

const form = (patch: Partial<TeamBookingForm> = {}): TeamBookingForm => ({
  unit: "320-03",
  start: START,
  name: "Casey Morgan",
  mobile: "",
  email: "",
  texts: false,
  ...patch,
});

test("a new form starts on the first unit with nothing picked", () => {
  assert.deepEqual(emptyBookingForm(units), {
    unit: "320-03",
    start: "",
    name: "",
    mobile: "",
    email: "",
    texts: false,
  });
  assert.equal(emptyBookingForm([]).unit, "");
});

test("the time picker offers the unit's offers as the page shows them, joins included", () => {
  assert.deepEqual(timeOptions(units, "320-03"), [
    { value: START, label: "Wed, Apr 14, 4:00 PM · Join, 2 places left" },
    { value: "2027-04-14T21:00:00Z", label: "Wed, Apr 14, 5:00 PM · Preferred" },
  ]);
  assert.deepEqual(timeOptions(units, "412-01"), []);
  assert.deepEqual(timeOptions(units, "nope"), []);
});

test("the body trims what was typed and asks for texts only with a mobile", () => {
  assert.deepEqual(bookingBody(form({ name: " Casey ", mobile: " 412-555-0119 ", texts: true })), {
    unit: "320-03",
    start: START,
    name: "Casey",
    mobile: "412-555-0119",
    email: "",
    texts: true,
  });
  assert.equal(bookingBody(form({ mobile: "  ", texts: true })).texts, false);
});

test("a unit, a time and a name are all the form needs", () => {
  assert.deepEqual(missingFields(form()), []);
  assert.deepEqual(missingFields(form({ unit: "", start: "", name: " " })), ["a unit", "a time", "a name"]);
});

test("the tab asks first only when something reaches the caller", () => {
  const modes = { live_units: [], live_all_units: false, lead_agent_uses_open_times: false };
  const capture = heldBackNote({ ...modes, send_mode: "capture" }, true, "320-03");
  const live = heldBackNote({ ...modes, send_mode: "live" }, true, "320-03");
  assert.equal(describeTeamBooking(form(), capture), null);
  assert.equal(describeTeamBooking(form({ mobile: "412-555-0119" }), capture), null);
  assert.equal(
    describeTeamBooking(form({ mobile: "412-555-0119", email: "casey@example.com", texts: true }), ""),
    "Book Casey Morgan for 320-03 on Wed, Apr 14, 4:00 PM? Casey Morgan gets a confirmation text at " +
      "412-555-0119 now, then a confirmation email at casey@example.com once it is delivered.",
  );
  assert.equal(
    describeTeamBooking(form({ mobile: "412-555-0119", texts: true }), capture),
    "Book Casey Morgan for 320-03 on Wed, Apr 14, 4:00 PM? Casey Morgan gets a confirmation text at " +
      "412-555-0119 now. Capture mode: these are kept under Captured messages, not sent.",
  );
  assert.equal(
    describeTeamBooking(form({ email: "casey@example.com" }), live),
    "Book Casey Morgan for 320-03 on Wed, Apr 14, 4:00 PM? Casey Morgan gets a confirmation email at " +
      "casey@example.com and no texts. 320-03 is not ticked for Live mode: these are captured, not sent.",
  );
});

test("each answer says what it queued", () => {
  assert.equal(describeQueued([]), "Nothing was queued.");
  assert.equal(
    describeQueued([
      { id: 1, kind: "prospect_confirmation", channel: "sms", status: "sent", held: false },
      { id: 2, kind: "host_event", channel: "calendar_event", status: "held", held: true },
      { id: 3, kind: "host_event_moved", channel: "calendar_event", status: "pending", held: false },
      { id: 4, kind: "resident_new_showing", channel: "sms", status: "held", held: true },
      { id: 5, kind: "some_new_kind", channel: "sms", status: "captured", held: false },
    ]),
    "Queued: confirmation text (sent), host's event (waits for the confirmation text), " +
      "old host's event cancelled (goes with the next run), residents' text (waits for the " +
      "confirmation text), some new kind (captured).",
  );
  assert.equal(
    describeBookingResult(
      {
        showing_id: 7,
        attendee_id: 9,
        kind: "join",
        texts_accepted: false,
        confirmation: null,
        notices: [{ id: 5, kind: "host_event_update", channel: "calendar_event", status: "pending", held: false }],
      },
      form(),
    ),
    "Booked Casey Morgan for 320-03 on Wed, Apr 14, 4:00 PM into the showing already booked. No texts. " +
      "Queued: host's event update (goes with the next run).",
  );
  assert.equal(
    describeHostChange({
      showing_id: 7,
      host_id: 2,
      host_name: "Sam Roe",
      previous_host_name: "Jane Doe",
      notices: [],
    }),
    "Sam Roe now hosts it instead of Jane Doe. Nothing was queued.",
  );
});

test("another host is any active agent but the one hosting", () => {
  const agents = [
    { agent_id: 1, name: "Jane Doe", status: "read" as const },
    { agent_id: 2, name: "Sam Roe", status: "sample" as const },
    { agent_id: 3, name: "Lee Park", status: "inactive" as const },
    { agent_id: 4, name: "Kim Ito", status: "unreadable" as const },
  ];
  assert.deepEqual(hostChoices(agents, { host_id: 1 }), [
    { id: 2, name: "Sam Roe" },
    { id: 4, name: "Kim Ito" },
  ]);
  assert.deepEqual(hostChoices([agents[0]], { host_id: 1 }), []);
});

test("pressing the mark already set clears it, the other one changes it", () => {
  assert.equal(attendanceAfter("unknown", "attended"), "attended");
  assert.equal(attendanceAfter("attended", "attended"), "unknown");
  assert.equal(attendanceAfter("attended", "no_show"), "no_show");
  assert.equal(describeAttendance({ name: "Alex" }, "no_show"), "Marked Alex: did not come.");
  assert.equal(describeAttendance({ name: "Alex" }, "unknown"), "Cleared the mark for Alex.");
});

test("a refusal's field lines are read for the form", () => {
  assert.deepEqual(
    refusalFields({ detail: { code: "invalid", message: "Check the marked fields.", fields: { name: "Enter it.", n: 3 } } }),
    { name: "Enter it." },
  );
  assert.deepEqual(refusalFields({ detail: "Not found" }), {});
  assert.deepEqual(refusalFields(null), {});
  assert.deepEqual(refusalFields({ detail: { fields: ["x"] } }), {});
});
