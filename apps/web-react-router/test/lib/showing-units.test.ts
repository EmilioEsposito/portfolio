import assert from "node:assert/strict";
import test from "node:test";

import { unitHeading, unitPath, unitTerms } from "~/lib/showing-units";

test("a unit's row: the address, then the unit", () => {
  assert.deepEqual(unitHeading({ unit: "320-03", address: "320 Example St" }), {
    title: "320 Example St",
    detail: "unit 320-03",
  });
  assert.deepEqual(unitHeading({ unit: "320-03", address: "" }), { title: "Unit 320-03", detail: null });
});

test("a unit's terms leave out what the listing lacks", () => {
  assert.equal(
    unitTerms({ rent: 1450, lease_start: "between Jun 1 and Jul 1, 2027" }),
    "$1,450 a month · Lease starts between Jun 1 and Jul 1, 2027",
  );
  assert.equal(unitTerms({ rent: null, lease_start: "on Jun 15, 2027" }), "Lease starts on Jun 15, 2027");
  assert.equal(unitTerms({ rent: 1200, lease_start: null }), "$1,200 a month");
  assert.equal(unitTerms({ rent: null, lease_start: null }), "");
});

test("each row links to the unit's booking page", () => {
  assert.equal(unitPath("320-03"), "/showings/320-03");
  assert.equal(unitPath("a/b"), "/showings/a%2Fb");
});
