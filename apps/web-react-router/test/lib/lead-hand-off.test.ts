import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import {
  HAND_OFF_END,
  HAND_OFF_START,
  handOffFromState,
  splitHandOff,
  withHandOff,
} from "../../app/sites/team/lib/lead-hand-off";

const block = `${HAND_OFF_START}\nPat is handing you a lead.\n\n<case>\nthe thread\n</case>\n${HAND_OFF_END}`;

test("a handed-off chat's first message shows as the person's words with the lead context apart", () => {
  const message = withHandOff(block, "  Cats are fine there. Tell them.  ");
  assert.ok(message.startsWith("Cats are fine there. Tell them.\n\n"));
  assert.deepEqual(splitHandOff(message), {
    words: "Cats are fine there. Tell them.",
    context: "Pat is handing you a lead.\n\n<case>\nthe thread\n</case>",
  });
});

test("an ordinary message is left whole, even one that mentions the block's first line", () => {
  for (const text of [
    "What rent is 12-C listed at?",
    `What does "${HAND_OFF_START}" mean?`,
    `Half a block\n${HAND_OFF_START}\nno end`,
  ]) {
    assert.deepEqual(splitHandOff(text), { context: null, words: text });
  }
});

test("the block's first and last lines match the ones the API writes", () => {
  const api = readFileSync(
    new URL("../../../../api/src/sernia_ai/lead_hand_off.py", import.meta.url),
    "utf8",
  );
  assert.ok(api.includes(`START = "${HAND_OFF_START}"`));
  assert.ok(api.includes(`END = "${HAND_OFF_END}"`));
});

test("a hand-off from a lead's page is taken from the router state only when whole", () => {
  const text = withHandOff(block, "Offer Saturday.");
  assert.deepEqual(handOffFromState({ handOff: { from: "zillow-t-1", text } }), { from: "zillow-t-1", text });
  for (const state of [
    null,
    undefined,
    {},
    { handOff: null },
    { handOff: { from: "", text } },
    { handOff: { from: "zillow-t-1" } },
    // Text without the block is an ordinary message, not a hand-off.
    { handOff: { from: "zillow-t-1", text: "Offer Saturday." } },
  ]) {
    assert.equal(handOffFromState(state), null, JSON.stringify(state));
  }
});
