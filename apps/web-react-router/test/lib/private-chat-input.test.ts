import assert from "node:assert/strict";
import test from "node:test";
import { privateChatInput } from "../../app/lib/private-chat-input";

test("old attachments stay in the UI but cannot exhaust the next request's size limit", () => {
  const history = [
    { id: "old", parts: [{ type: "file", url: "data:image/png;base64," + "a".repeat(17 * 1024 * 1024) }] },
    { id: "new", parts: [{ type: "text", text: "Continue" }] },
  ];
  assert.deepEqual(privateChatInput(history), [history[1]]);
  assert.equal(history.length, 2);
  assert.ok(JSON.stringify(privateChatInput(history)).length < 100);
});

test("latest attachments remain intact and empty input stays empty", () => {
  const latest = { id: "new", parts: [{ type: "file", url: "data:image/png;base64,abc" }] };
  assert.deepEqual(privateChatInput([latest]), [latest]);
  assert.deepEqual(privateChatInput([]), []);
});
