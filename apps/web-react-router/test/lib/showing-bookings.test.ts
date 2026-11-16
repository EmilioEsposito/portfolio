import assert from "node:assert/strict";
import test from "node:test";

import {
  type BookingSettings,
  type DrainReport,
  CONNECT_RESULTS,
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
  refusalMessage,
  sameSettings,
  startsLiveSends,
} from "../../app/lib/showing-bookings.ts";

const settings = (patch: Partial<BookingSettings> = {}): BookingSettings => ({
  send_mode: "capture",
  live_units: [],
  test_sends_enabled: false,
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
