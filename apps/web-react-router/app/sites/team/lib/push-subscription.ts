/**
 * The team's service worker (public/team/sw.js). Served from /team/, so its
 * scope is /team/: it controls the pages under /team/ and nothing else. The
 * team home at /team itself is outside that scope; the install manifest
 * (public/team/manifest.json) covers it too.
 */
export const TEAM_SERVICE_WORKER_URL = "/team/sw.js";
export const TEAM_SERVICE_WORKER_SCOPE = "/team/";

/** Where the browser remembers which signed-in user its push subscription belongs to. */
export const PUSH_USER_STORAGE_KEY = "sernia-push-user-id";

type SubscriptionHolder = {
  pushManager: { getSubscription(): Promise<{ unsubscribe(): Promise<boolean> } | null> };
};

/**
 * Ends this browser's team push subscription and forgets whose it was, for a
 * browser no team member is signed in on. The push service then refuses the
 * endpoint, and the API drops it the next time it sends. Answers whether there
 * was a subscription to end.
 */
export async function revokeTeamPush(
  serviceWorker: { getRegistration(scope: string): Promise<SubscriptionHolder | undefined> },
  storage: Pick<Storage, "removeItem">,
): Promise<boolean> {
  const registration = await serviceWorker.getRegistration(TEAM_SERVICE_WORKER_SCOPE);
  const subscription = (await registration?.pushManager.getSubscription()) ?? null;
  if (subscription) await subscription.unsubscribe();
  storage.removeItem(PUSH_USER_STORAGE_KEY);
  return subscription !== null;
}

/**
 * Tells the team's service worker that a team page is open and visible, so
 * the notifications shown until now stop counting toward the app icon's
 * badge (public/team/sw.js). Does nothing in a browser with no worker yet.
 */
export async function tellWorkerAppOpened(serviceWorker: {
  getRegistration(scope: string): Promise<{ active?: { postMessage(message: unknown): void } | null } | undefined>;
}): Promise<void> {
  const registration = await serviceWorker.getRegistration(TEAM_SERVICE_WORKER_SCOPE);
  registration?.active?.postMessage({ type: "app-opened" });
}

type ActivatingRegistration = Pick<ServiceWorkerRegistration, "active" | "installing" | "waiting">;

/**
 * Resolves once this registration has an active worker, which a push
 * subscription needs. Not `navigator.serviceWorker.ready`: that waits for a
 * registration that controls the current page, and the team home at `/team`
 * is outside the `/team/` scope, so on a new browser it would never resolve.
 */
export function whenActive<T extends ActivatingRegistration>(registration: T): Promise<T> {
  if (registration.active) return Promise.resolve(registration);
  const worker = registration.installing ?? registration.waiting;
  if (!worker) return Promise.reject(new Error("The notification worker did not install"));
  return new Promise((resolve, reject) => {
    const settle = () => {
      if (worker.state === "activated") {
        worker.removeEventListener("statechange", settle);
        resolve(registration);
      } else if (worker.state === "redundant") {
        worker.removeEventListener("statechange", settle);
        reject(new Error("The notification worker did not install"));
      }
    };
    worker.addEventListener("statechange", settle);
    settle();
  });
}

function decodePublicKey(publicKey: string): Uint8Array<ArrayBuffer> {
  if (!publicKey) throw new Error("Notification key is not configured");
  const base64 = publicKey.replace(/-/g, "+").replace(/_/g, "/");
  return Uint8Array.from(atob(base64), (character) => character.charCodeAt(0));
}

/** Reuse only subscriptions signed by the server's current VAPID identity. */
export async function ensureCurrentPushSubscription(
  manager: Pick<PushManager, "getSubscription" | "subscribe">,
  publicKey: string,
): Promise<PushSubscription> {
  const expected = decodePublicKey(publicKey);
  const existing = await manager.getSubscription();
  if (existing) {
    const boundKey = existing.options.applicationServerKey;
    const actual = boundKey ? new Uint8Array(boundKey) : null;
    if (
      actual?.length === expected.length &&
      actual.every((byte, index) => byte === expected[index])
    ) {
      return existing;
    }
    await existing.unsubscribe();
    if (await manager.getSubscription()) {
      throw new Error("Could not retire the previous notification subscription");
    }
  }
  return manager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: expected.buffer,
  });
}
