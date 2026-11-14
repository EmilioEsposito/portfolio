import assert from "node:assert/strict";
import test from "node:test";

import {
  type Change,
  type RentalListing,
  cleanRules,
  cleanListing,
  describeChange,
  describeHistory,
  isOffMarket,
  newListing,
  problemsFrom,
  sameRules,
  sameListing,
  zillowHref,
} from "../../app/lib/rental-listings.ts";

const unit = (patch: Partial<RentalListing> = {}): RentalListing => ({ ...newListing("12-A"), ...patch });

const change = (subject: string, snapshot: Record<string, unknown>): Change => ({
  subject,
  snapshot,
  changed_at: "2027-01-01T12:00:00Z",
  changed_by: "Pat Example",
  source: "rental units screen",
});

test("a unit is sent trimmed, without blank lines, with days in week order", () => {
  const cleaned = cleanListing(
    unit({
      address: "  12 Example St ",
      off_market_on: "",
      public_facts: [" Dishwasher ", "", "   "],
      internal_notes: ["", "Resident wants notice"],
      preferred_showing_times: [{ days: ["fri", "mon"], start: "16:00:00", end: "18:00" }],
    }),
  );
  assert.equal(cleaned.address, "12 Example St");
  assert.equal(cleaned.off_market_on, null);
  assert.deepEqual(cleaned.public_facts, ["Dishwasher"]);
  assert.deepEqual(cleaned.internal_notes, ["Resident wants notice"]);
  assert.deepEqual(cleaned.preferred_showing_times, [{ days: ["mon", "fri"], start: "16:00", end: "18:00" }]);
});

test("an edit that only adds blank lines or changes time format is not a change", () => {
  const saved = unit({
    public_facts: ["Dishwasher"],
    preferred_showing_times: [{ days: ["mon"], start: "16:00:00", end: "18:00:00" }],
  });
  const edited = unit({
    public_facts: ["Dishwasher", ""],
    preferred_showing_times: [{ days: ["mon"], start: "16:00", end: "18:00" }],
  });
  assert.ok(sameListing(saved, edited));
  assert.ok(!sameListing(saved, unit({ ...saved, asking_rent: 1500 })));
});

test("blackout dates are sent sorted, once each", () => {
  const rules = {
    timezone: "America/New_York",
    earliest_start: "09:00:00",
    latest_start: "18:00:00",
    blackout_dates: ["2027-07-04", "2027-01-01", "2027-07-04", ""],
    showing_booking_url: "",
  };
  assert.deepEqual(cleanRules(rules).blackout_dates, ["2027-01-01", "2027-07-04"]);
  assert.ok(sameRules(rules, { ...rules, earliest_start: "09:00", blackout_dates: ["2027-01-01", "2027-07-04"] }));
});

test("a unit is off the market from its off-market date on, not before", () => {
  assert.equal(isOffMarket(unit(), "2027-06-01"), false);
  assert.equal(isOffMarket(unit({ off_market_on: "2027-06-15" }), "2027-06-01"), false);
  assert.equal(isOffMarket(unit({ off_market_on: "2027-06-15" }), "2027-06-15"), true);
});

test("the server's reasons for refusing a save become lines to show", () => {
  const checks = problemsFrom(422, {
    detail: { message: "Not saved.", problems: ["unit 12-A internal note 'Door code': mentions an access code"] },
  });
  assert.deepEqual(checks, {
    message: "Not saved.",
    problems: ["unit 12-A internal note 'Door code': mentions an access code"],
  });

  const types = problemsFrom(422, {
    detail: [{ loc: ["body", "unit", "asking_rent"], msg: "Input should be a valid integer" }],
  });
  assert.deepEqual(types.problems, ["asking_rent: Input should be a valid integer"]);

  assert.deepEqual(problemsFrom(404, { detail: "No unit named '12-B'." }), {
    message: "No unit named '12-B'.",
    problems: [],
  });
  assert.match(problemsFrom(401, null).message, /signed out/);
  assert.match(problemsFrom(500, null).message, /500/);
});

test("a change is described field by field against the one before it", () => {
  const before = { asking_rent: 1500, off_market_on: null, public_facts: ["Dishwasher"], virtual_tours_allowed: false };
  const after = {
    asking_rent: 1550,
    off_market_on: "2027-06-15",
    public_facts: ["Dishwasher", "Washer and dryer"],
    virtual_tours_allowed: true,
  };
  assert.deepEqual(describeChange(after, before), [
    "Asking rent: 1500 → 1550",
    "Off market from: none → 2027-06-15",
    "Public facts: added “Washer and dryer”",
    "Virtual tours allowed: no → yes",
  ]);
  assert.deepEqual(describeChange(before, null), ["Created"]);
  assert.deepEqual(describeChange(before, before), ["No change"]);
});

test("history compares each change with the previous one to the same subject", () => {
  const history = [
    change("12-A", { asking_rent: 1600 }),
    change("showing_rules", { blackout_dates: ["2027-07-04"] }),
    change("12-A", { asking_rent: 1500 }),
  ];
  const complete = describeHistory(history, true).map(({ lines }) => lines);
  assert.deepEqual(complete, [["Asking rent: 1500 → 1600"], ["First saved"], ["Created"]]);

  const cut = describeHistory(history, false).map(({ lines }) => lines);
  assert.deepEqual(cut[2], ["Earlier changes not shown"]);
});

test("a Zillow ad link is offered to open only when it is plain https on zillow.com", () => {
  assert.equal(
    zillowHref(" https://www.zillow.com/homedetails/x/1_zpid/ "),
    "https://www.zillow.com/homedetails/x/1_zpid/",
  );
  assert.equal(zillowHref("https://zillow.com/rental-manager/x"), "https://zillow.com/rental-manager/x");
  for (const value of [
    "",
    "zillow.com/homedetails/x",
    "http://www.zillow.com/x",
    "https://zillow.com.example.com/x",
    "https://notzillow.com/x",
    "https://user@www.zillow.com/x",
    "https://www.zillow.com:8443/x",
    "javascript:alert(1)",
  ]) {
    assert.equal(zillowHref(value), null, value);
  }
});

test("the Zillow ad links are trimmed and named in history", () => {
  const edited = unit({ zillow_listing_url_public_view: "  https://www.zillow.com/x  " });
  assert.equal(cleanListing(edited).zillow_listing_url_public_view, "https://www.zillow.com/x");
  assert.deepEqual(
    describeChange(
      { ...newListing("12-A"), zillow_listing_url_public_view: "https://www.zillow.com/x" },
      { ...newListing("12-A") },
    ),
    ["Zillow ad (public): none → https://www.zillow.com/x"],
  );
});
