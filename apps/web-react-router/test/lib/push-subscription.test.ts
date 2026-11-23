import assert from "node:assert/strict";
import test from "node:test";
import {
  ensureCurrentPushSubscription,
  PUSH_USER_STORAGE_KEY,
  revokeTeamPush,
  TEAM_SERVICE_WORKER_SCOPE,
  whenActive,
} from "../../app/sites/team/lib/push-subscription.ts";

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

// A worker whose state the test moves along, as the browser would.
function fakeWorker(state: ServiceWorkerState) {
  const listeners = new Set<() => void>();
  return {
    state,
    addEventListener: (_type: string, listener: () => void) => listeners.add(listener),
    removeEventListener: (_type: string, listener: () => void) => listeners.delete(listener),
    become(next: ServiceWorkerState) {
      this.state = next;
      for (const listener of [...listeners]) listener();
    },
    listening: () => listeners.size,
  };
}
const asWorker = (worker: ReturnType<typeof fakeWorker>) => worker as unknown as ServiceWorker;

test("an active worker is ready at once", async () => {
  const registration = { active: asWorker(fakeWorker("activated")), installing: null, waiting: null };
  assert.equal(await whenActive(registration), registration);
});

test("a worker still installing is waited for, on a page it does not control", async () => {
  // The team home at /team is outside the /team/ scope, so nothing here may
  // depend on the page being controlled: only on this registration's worker.
  const worker = fakeWorker("installing");
  const registration = { active: null, installing: asWorker(worker), waiting: null };
  let ready = false;
  const waiting = whenActive(registration).then(() => (ready = true));
  worker.become("installed");
  await Promise.resolve();
  assert.equal(ready, false);
  worker.become("activating");
  worker.become("activated");
  await waiting;
  assert.equal(ready, true);
  assert.equal(worker.listening(), 0);
});

test("a worker that fails to install is reported, not waited for forever", async () => {
  const worker = fakeWorker("installing");
  const waiting = whenActive({ active: null, installing: asWorker(worker), waiting: null });
  worker.become("redundant");
  await assert.rejects(waiting, /did not install/);
  await assert.rejects(whenActive({ active: null, installing: null, waiting: null }), /did not install/);
});


// A browser with (or without) the team's registration and a push subscription on it.
function browser(registered: boolean, subscribed: boolean) {
  const events: string[] = [];
  const subscription = { unsubscribe: async () => (events.push("unsubscribe"), true) };
  return {
    events,
    serviceWorker: {
      getRegistration: async (scope: string) => {
        events.push(`registration ${scope}`);
        return registered ? { pushManager: { getSubscription: async () => (subscribed ? subscription : null) } } : undefined;
      },
    },
    storage: { removeItem: (key: string) => void events.push(`forget ${key}`) },
  };
}

test("ending notifications unsubscribes the team's registration and forgets its owner", async () => {
  const b = browser(true, true);
  assert.equal(await revokeTeamPush(b.serviceWorker, b.storage), true);
  assert.deepEqual(b.events, [
    `registration ${TEAM_SERVICE_WORKER_SCOPE}`,
    "unsubscribe",
    `forget ${PUSH_USER_STORAGE_KEY}`,
  ]);
});

test("ending notifications on a browser without them only forgets the owner", async () => {
  for (const b of [browser(true, false), browser(false, false)]) {
    assert.equal(await revokeTeamPush(b.serviceWorker, b.storage), false);
    assert.deepEqual(b.events.slice(1), [`forget ${PUSH_USER_STORAGE_KEY}`]);
  }
});
