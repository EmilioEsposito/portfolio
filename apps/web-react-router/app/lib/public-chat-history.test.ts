import assert from "node:assert/strict";
import test from "node:test";
import type { UIMessage } from "ai";
import { publicChatHistory } from "./public-chat-history";

// Captured from the failing portfolio -> NYC weather browser request. Tool and
// reasoning contents are replaced with dummy text, preserving its 12-part shape
// and output sizes; no scraped documents or reasoning are retained as fixtures.
const capturedHistory = [
  { id: "user1", role: "user", parts: [{ type: "text", text: "How does Emilio turn an AI prototype into a reliable product?" }] },
  { id: "assistant1", role: "assistant", parts: [
    { type: "step-start" }, { type: "reasoning", text: "r".repeat(452) }, { type: "reasoning", text: "" },
    { type: "tool-fetch_resume", toolCallId: "resume", state: "output-available", input: {}, output: "x".repeat(16495) },
    { type: "step-start" }, { type: "reasoning", text: "r".repeat(481) }, { type: "reasoning", text: "" },
    { type: "tool-fetch_interview_ai_launch", toolCallId: "interview", state: "output-available", input: {}, output: "x".repeat(16307) },
    { type: "step-start" }, { type: "reasoning", text: "r".repeat(433) }, { type: "reasoning", text: "" },
    { type: "text", text: "Public portfolio answer." },
  ] },
  { id: "user2", role: "user", parts: [{ type: "text", text: "and what's weather in nyc?" }] },
] as UIMessage[];

test("captured portfolio tool history stays visible but no longer breaks the next request", () => {
  const original = JSON.stringify(capturedHistory);
  assert.ok(new TextEncoder().encode(original).length > 24000);
  assert.equal(capturedHistory[1].parts.length, 12);
  const outbound = publicChatHistory(capturedHistory);
  assert.deepEqual(outbound.map((message) => message.parts), [
    [{ type: "text", text: "How does Emilio turn an AI prototype into a reliable product?" }],
    [{ type: "text", text: "Public portfolio answer." }],
    [{ type: "text", text: "and what's weather in nyc?" }],
  ]);
  assert.ok(JSON.stringify(outbound).length < 24000);
  assert.equal(JSON.stringify(capturedHistory), original);
});

test("history obeys character, part, message and UTF8 byte limits while retaining latest user", () => {
  const messages = Array.from({ length: 40 }, (_, i) => ({
    id: String(i), role: i % 2 ? "user" : "assistant", parts: [{ type: "text", text: "界".repeat(4000) }],
  })) as UIMessage[];
  const outbound = publicChatHistory(messages);
  assert.equal(outbound.at(-1)?.id, "39");
  assert.ok(outbound.length <= 16);
  assert.ok(outbound.every((message) => message.parts.length === 1));
  assert.ok(new TextEncoder().encode(JSON.stringify(outbound)).length < 24000);
  assert.ok(outbound.flatMap((message) => message.parts).reduce((size, part) => size + part.text.length, 0) <= 12000);
});
