import assert from "node:assert/strict";
import test from "node:test";

import { NETWORK_MESSAGE, SERVER_MESSAGE, type BookingView } from "../../app/sites/sernia-public/lib/showing-booking-page.ts";
import {
  UNITS_PATH,
  UNKNOWN_MESSAGE,
  calendarFileToken,
  cancelCopy,
  cancelPath,
  cancelState,
  readCancelAnswer,
  unitPath,
} from "../../app/sites/sernia-public/lib/showing-cancel-page.ts";

const view = (patch: Partial<BookingView> = {}): BookingView => ({
  status: "booked",
  unit: "320-03",
  address: "320 Example St",
  starts_at: "2027-04-14T20:00:00Z",
  ends_at: "2027-04-14T20:30:00Z",
  timezone: "America/New_York",
  mobile: "•••-•••-••17",
  texted: true,
  corrections_left: 2,
  started: false,
  cancelled_by_team: false,
  ...patch,
});

test("each booking reads as one state", () => {
  assert.equal(cancelState(view()), "booked");
  assert.equal(cancelState(view({ started: true })), "started");
  assert.equal(cancelState(view({ status: "cancelled" })), "already_cancelled");
  assert.equal(cancelState(view({ status: "cancelled" }), true), "cancelled_now");
  // A team cancellation reads as the team's, even right after pressing Cancel.
  assert.equal(cancelState(view({ status: "cancelled", cancelled_by_team: true })), "cancelled_by_team");
  assert.equal(cancelState(view({ status: "cancelled", cancelled_by_team: true }), true), "cancelled_by_team");
  // Cancelled wins over started: there is nothing left to start.
  assert.equal(cancelState(view({ status: "cancelled", started: true })), "already_cancelled");
});

test("after cancelling, the page points to another time at the same unit", () => {
  assert.deepEqual(cancelCopy("cancelled_now", "320-03").next, { label: "Book another time", href: "/showings/320-03" });
  assert.deepEqual(cancelCopy("already_cancelled", "320-03").next?.href, "/showings/320-03");
  assert.deepEqual(cancelCopy("cancelled_by_team", "320-03").next, {
    label: "See units open for showings",
    href: UNITS_PATH,
  });
  assert.equal(cancelCopy("booked", "320-03").next, null);
  assert.equal(cancelCopy("started", "320-03").next, null);
  assert.equal(cancelCopy("booked", "320-03").heading, "Cancel your showing?");
  assert.equal(cancelCopy("cancelled_by_team", "320-03").heading, "We cancelled this showing");
});

test("paths encode what they carry", () => {
  assert.equal(cancelPath("abc_DEF-123"), "/showings/cancel/abc_DEF-123");
  assert.equal(cancelPath("a/b?c"), "/showings/cancel/a%2Fb%3Fc");
  assert.equal(unitPath("12/14"), "/showings/12%2F14");
});

test("the cancel route's answers become what the page shows", () => {
  const cancelled = view({ status: "cancelled" });
  assert.deepEqual(readCancelAnswer(200, cancelled), { kind: "view", view: cancelled });
  const started = { detail: { code: "started", message: "This showing has already started.", fields: {} } };
  assert.deepEqual(readCancelAnswer(409, started), { kind: "started" });
  const unknown = { detail: { code: "unknown_booking", message: "We couldn't find this booking.", fields: {} } };
  assert.deepEqual(readCancelAnswer(404, unknown), { kind: "unknown", message: UNKNOWN_MESSAGE });
  // Booking switched off answers a bare 404.
  assert.deepEqual(readCancelAnswer(404, { detail: "Not Found" }), { kind: "unknown", message: UNKNOWN_MESSAGE });
  assert.deepEqual(readCancelAnswer(null, null), { kind: "error", message: NETWORK_MESSAGE });
  assert.deepEqual(readCancelAnswer(500, null), { kind: "error", message: SERVER_MESSAGE });
  assert.deepEqual(readCancelAnswer(200, null), { kind: "error", message: SERVER_MESSAGE });
});

test("the calendar link names the booking by its cancel token", () => {
  assert.equal(calendarFileToken("WwmrkG16iYYgfFat-_QT4lFG.ics"), "WwmrkG16iYYgfFat-_QT4lFG");
  for (const file of [undefined, "", ".ics", "token", "token.ICS", "a/b.ics", "a b.ics", "token.ics.ics", `${"x".repeat(101)}.ics`]) {
    assert.equal(calendarFileToken(file), null, String(file));
  }
});
