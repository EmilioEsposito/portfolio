import assert from "node:assert/strict";
import test from "node:test";
import {
  currentStage,
  findItem,
  items,
  litSplits,
  phases,
  splits,
  stageIsRelated,
  stages,
  totals,
} from "../../app/lib/evals-map";

test("every stage and split has one id, a term, and something to show", () => {
  const ids = items.map((item) => item.id);
  assert.equal(new Set(ids).size, ids.length);
  for (const item of items) {
    assert.ok(item.term && item.oneLine && item.here, item.id);
    assert.ok(item.what.length > 0 && item.example.blocks.length > 0, item.id);
    assert.equal(findItem(item.id), item);
  }
});

test("each stage belongs to a known phase and touches only known splits", () => {
  const phaseIds = new Set(phases.map((phase) => phase.id));
  const splitIds = new Set(splits.map((split) => split.id));
  for (const stage of stages) {
    assert.ok(phaseIds.has(stage.phase), stage.id);
    for (const split of stage.touches) assert.ok(splitIds.has(split), stage.id);
  }
  for (const phase of phases) {
    assert.ok(stages.some((stage) => stage.phase === phase.id), phase.id);
  }
});

test("the map opens on the one stage in progress", () => {
  assert.deepEqual(
    stages.filter((stage) => stage.status === "now").map((stage) => stage.id),
    [currentStage.id],
  );
  // Nothing after the current stage in its phase can already be done.
  const phase = stages.filter((stage) => stage.phase === currentStage.phase);
  const after = phase.slice(phase.indexOf(currentStage) + 1);
  assert.ok(after.every((stage) => stage.status === "next"));
});

test("split sizes add up and the cases under review sit in dev", () => {
  assert.deepEqual(totals, { threads: 143, cases: 353 });
  for (const split of splits) {
    assert.ok(split.inReview <= split.cases);
    assert.equal(split.inReview > 0, split.id === "dev");
  }
});

test("a stage lights the splits it uses, and a split lights the stages that use it", () => {
  const heldOut = stages.find((stage) => stage.id === "held-out-test")!;
  const annotation = stages.find((stage) => stage.id === "annotation")!;
  const testSplit = splits.find((split) => split.id === "test")!;

  assert.deepEqual(litSplits(heldOut), ["test"]);
  assert.deepEqual(litSplits(testSplit), ["test"]);
  assert.equal(stageIsRelated(heldOut, testSplit), true);
  assert.equal(stageIsRelated(annotation, testSplit), false);
  // Nothing before the final gate may read the test split, except assigning it.
  const readers = stages.filter((stage) => stage.touches.includes("test")).map((s) => s.id);
  assert.deepEqual(readers, ["split", "held-out-test", "dataset-growth"]);
});
