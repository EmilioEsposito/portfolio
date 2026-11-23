import assert from "node:assert/strict";
import test from "node:test";

import { loader } from "~/sites/team/routes/leads/bookings";

// The Booking settings tab says when public booking is on but this environment
// does not serve the booking pages, which only the web app knows.

const NAMES = ["SERNIA_PUBLIC_PAGES_ENABLED", "RAILWAY_ENVIRONMENT_NAME"] as const;

function withEnvironment(environment: Partial<Record<(typeof NAMES)[number], string>>) {
  const before = NAMES.map((name) => process.env[name]);
  for (const name of NAMES) {
    if (environment[name] === undefined) delete process.env[name];
    else process.env[name] = environment[name];
  }
  try {
    return loader().publicPagesOn;
  } finally {
    NAMES.forEach((name, at) => {
      if (before[at] === undefined) delete process.env[name];
      else process.env[name] = before[at];
    });
  }
}

test("the tab knows whether this environment serves the public booking pages", () => {
  assert.equal(withEnvironment({}), true);
  assert.equal(withEnvironment({ SERNIA_PUBLIC_PAGES_ENABLED: "false" }), false);
  assert.equal(withEnvironment({ RAILWAY_ENVIRONMENT_NAME: "production" }), false);
  assert.equal(withEnvironment({ RAILWAY_ENVIRONMENT_NAME: "production", SERNIA_PUBLIC_PAGES_ENABLED: "true" }), true);
});
