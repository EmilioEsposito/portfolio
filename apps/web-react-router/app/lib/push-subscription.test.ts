import assert from "node:assert/strict";
import test from "node:test";
import { ensureCurrentPushSubscription } from "./push-subscription.ts";

const key = new Uint8Array([4, 251, 255, 19]);
const encoded = Buffer.from(key).toString("base64url");

function fixture(boundKey: Uint8Array | null, removalWorks = true) {
  const events: string[] = [];
  let current: PushSubscription | null;
  const original = {
    options: { applicationServerKey: boundKey?.buffer ?? null },
    unsubscribe: async () => {
      events.push("unsubscribe");
      if (removalWorks) current = null;
      return removalWorks;
    },
  } as PushSubscription;
  current = original;
  const replacement = { options: { applicationServerKey: key.buffer } } as PushSubscription;
  const manager = {
    getSubscription: async () => current,
    subscribe: async (options: PushSubscriptionOptionsInit) => {
      events.push("subscribe");
      assert.equal(current, null, "retire the stale subscription before replacement");
      assert.equal(options.userVisibleOnly, true);
      assert.deepEqual(new Uint8Array(options.applicationServerKey as ArrayBuffer), key);
      current = replacement;
      return replacement;
    },
  };
  return { manager, events, original, replacement };
}

test("same VAPID identity reuses an existing subscription without churn", async () => {
  const f = fixture(key);
  assert.equal(await ensureCurrentPushSubscription(f.manager, encoded), f.original);
  assert.deepEqual(f.events, []);
});

for (const stale of [new Uint8Array([4, 251, 255, 18]), null]) {
  test(`rotated or unbound VAPID identity is replaced (${stale === null ? "legacy" : "rotated"})`, async () => {
    const f = fixture(stale);
    assert.equal(await ensureCurrentPushSubscription(f.manager, encoded), f.replacement);
    assert.deepEqual(f.events, ["unsubscribe", "subscribe"]);
  });
}

test("failed retirement cannot silently reuse the old subscription", async () => {
  const f = fixture(null, false);
  await assert.rejects(ensureCurrentPushSubscription(f.manager, encoded), /Could not retire/);
  assert.deepEqual(f.events, ["unsubscribe"]);
});

test("missing server key preserves existing subscriptions and reports failure", async () => {
  const f = fixture(key);
  await assert.rejects(ensureCurrentPushSubscription(f.manager, ""), /not configured/);
  assert.deepEqual(f.events, []);
});

test("browser rejection is surfaced instead of reporting successful renewal", async () => {
  const f = fixture(null);
  f.manager.subscribe = async () => { throw new Error("user gesture required"); };
  await assert.rejects(ensureCurrentPushSubscription(f.manager, encoded), /user gesture/);
  assert.deepEqual(f.events, ["unsubscribe"]);
});
