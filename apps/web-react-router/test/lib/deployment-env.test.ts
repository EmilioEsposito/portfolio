import assert from "node:assert/strict";
import test from "node:test";

import { getDeploymentEnvironment } from "~/lib/deployment-env";

test("uses Railway environment identity instead of the request hostname", () => {
  assert.equal(getDeploymentEnvironment("production"), "production");
  assert.equal(getDeploymentEnvironment("development"), "development");
  assert.equal(getDeploymentEnvironment("portfolio-pr-307"), "preview");
});

test("treats an absent Railway environment as local development", () => {
  assert.equal(getDeploymentEnvironment(undefined), "local");
  assert.equal(getDeploymentEnvironment(""), "local");
});
