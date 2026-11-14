import assert from "node:assert/strict";
import test from "node:test";
import {
  mergeConversationSummaries,
  type ConversationSummary,
} from "../../app/lib/sernia-conversations.ts";

const conversation = (
  id: string,
  requestId: string,
  status: "running" | "completed",
  date: string,
): ConversationSummary => ({
  conversation_id: id,
  modality: "web_chat",
  preview: id,
  has_pending: false,
  trigger_source: null,
  trigger_contact_name: null,
  created_at: date,
  updated_at: date,
  run: { id: requestId, status, started_at: date },
});

test("a stale sidebar fetch cannot erase a just-submitted conversation", () => {
  const earlier = conversation("old", "old-run", "completed", "2026-11-12");
  const pending = conversation("new", "new-run", "running", "2026-11-13");
  assert.deepEqual(mergeConversationSummaries([earlier], [pending]), [
    pending,
    earlier,
  ]);
});

test("a server receipt replaces the optimistic entry without duplication", () => {
  const pending = conversation("new", "run", "running", "2026-11-13");
  const saved = {
    ...pending,
    preview: "Saved title",
    run: { ...pending.run!, status: "completed" as const },
  };
  assert.deepEqual(mergeConversationSummaries([saved], [pending]), [saved]);
});

test("an earlier completed turn cannot hide a new running turn in the same conversation", () => {
  const saved = conversation("chat", "first", "completed", "2026-11-12");
  const pending = {
    ...conversation("chat", "second", "running", "2026-11-13"),
    preview: "follow-up",
  };
  const result = mergeConversationSummaries([saved], [pending]);
  assert.equal(result.length, 1);
  assert.equal(result[0].run?.id, "second");
  assert.equal(result[0].preview, "chat");
  assert.equal(result[0].created_at, saved.created_at);
});
