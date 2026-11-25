import assert from "node:assert/strict";
import test from "node:test";
import {
  inFront,
  PRESENCE_INTERVAL_MS,
  watchPresence,
  type PresenceReport,
} from "../../app/sites/team/lib/conversation-presence.ts";

/** A page whose visibility, focus and kind of screen a test sets. */
function fakePage({ touch = false } = {}) {
  const listeners = new Map<string, Set<() => void>>();
  const on = (name: string, handler: () => void) => {
    if (!listeners.has(name)) listeners.set(name, new Set());
    listeners.get(name)!.add(handler);
  };
  const off = (name: string, handler: () => void) => listeners.get(name)?.delete(handler);
  const fire = (name: string) => listeners.get(name)?.forEach((handler) => handler());
  let tick: (() => void) | null = null;
  const state = { visible: true, focused: true };

  const doc = {
    get visibilityState() {
      return state.visible ? ("visible" as const) : ("hidden" as const);
    },
    hasFocus: () => state.focused,
    addEventListener: on,
    removeEventListener: off,
  };
  const win = {
    matchMedia: (query: string) => ({ matches: touch && query === "(hover: none)" }),
    setInterval(handler: () => void, timeout: number) {
      assert.equal(timeout, PRESENCE_INTERVAL_MS);
      tick = handler;
      return 1;
    },
    clearInterval() {
      tick = null;
    },
    addEventListener: on,
    removeEventListener: off,
  };
  return {
    doc,
    win,
    state,
    fire,
    tick: () => tick?.(),
    listening: () => [...listeners.values()].reduce((count, set) => count + set.size, 0),
  };
}

function watch(page: ReturnType<typeof fakePage>, conversationId = "conversation-123") {
  const sent: PresenceReport[] = [];
  const stop = watchPresence(conversationId, (report) => sent.push(report), page.doc, page.win);
  const shown = () => sent.map((report) => report.conversation_id);
  return { sent, shown, stop };
}

test("on a computer, a visible window must also have the focus", () => {
  const page = fakePage();
  assert.equal(inFront(page.doc, page.win), true);
  page.state.focused = false;
  assert.equal(inFront(page.doc, page.win), false);
  page.state.focused = true;
  page.state.visible = false;
  assert.equal(inFront(page.doc, page.win), false);
});

test("on a phone, the app on screen is in front whatever it says about focus", () => {
  const page = fakePage({ touch: true });
  page.state.focused = false;
  assert.equal(inFront(page.doc, page.win), true);
  page.state.visible = false;
  assert.equal(inFront(page.doc, page.win), false);
});

test("reports the conversation, repeats it while in front, and reports leaving once", () => {
  const page = fakePage({ touch: true });
  const { shown, stop } = watch(page);
  assert.deepEqual(shown(), ["conversation-123"]);

  page.tick();
  assert.deepEqual(shown(), ["conversation-123", "conversation-123"]);

  // Switching to another app: one "left", then nothing until it comes back.
  page.state.visible = false;
  page.fire("visibilitychange");
  page.tick();
  page.fire("blur");
  assert.deepEqual(shown(), ["conversation-123", "conversation-123", null]);

  page.state.visible = true;
  page.fire("visibilitychange");
  page.fire("focus");
  assert.deepEqual(shown(), ["conversation-123", "conversation-123", null, "conversation-123"]);

  stop();
  assert.deepEqual(shown().at(-1), null);
  assert.equal(page.listening(), 0);
});

test("a window that loses the focus on a computer reports leaving", () => {
  const page = fakePage();
  const { shown, stop } = watch(page);
  page.state.focused = false;
  page.fire("blur");
  assert.deepEqual(shown(), ["conversation-123", null]);
  stop();
  // It had already said it left.
  assert.deepEqual(shown(), ["conversation-123", null]);
});

test("a page opened in the background says nothing until it is in front", () => {
  const page = fakePage();
  page.state.visible = false;
  const { shown, stop } = watch(page);
  page.tick();
  assert.deepEqual(shown(), []);
  stop();
  assert.deepEqual(shown(), []);
});

test("closing the page reports leaving", () => {
  const page = fakePage();
  const { shown } = watch(page);
  page.fire("pagehide");
  assert.deepEqual(shown(), ["conversation-123", null]);
});

test("reports from one page share its ID and count up across conversations", () => {
  const page = fakePage();
  const first = watch(page, "conversation-a");
  first.stop();
  const second = watch(page, "conversation-b");
  const reports = [...first.sent, ...second.sent];
  assert.deepEqual(
    reports.map((report) => report.conversation_id),
    ["conversation-a", null, "conversation-b"],
  );
  assert.equal(new Set(reports.map((report) => report.client_id)).size, 1);
  const seqs = reports.map((report) => report.seq);
  assert.deepEqual(seqs, [...seqs].sort((a, b) => a - b));
  assert.equal(new Set(seqs).size, seqs.length);
  second.stop();
});
