import { useState, useEffect, useCallback, useRef } from "react";
import { useAuth } from "@clerk/react-router";
import {
  ensureCurrentPushSubscription,
  PUSH_USER_STORAGE_KEY,
  TEAM_SERVICE_WORKER_SCOPE,
  TEAM_SERVICE_WORKER_URL,
  whenActive,
} from "~/sites/team/lib/push-subscription";
import { readDevice, type DeviceFacts } from "~/sites/team/lib/team-device";

const API_BASE = "/api/sernia-ai";

async function fetchPublicKey(token: string): Promise<string> {
  const response = await fetch(`${API_BASE}/push/vapid-public-key`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!response.ok) {
    throw new Error(`Failed to load notification key (${response.status})`);
  }
  const { publicKey } = await response.json();
  if (typeof publicKey !== "string" || !publicKey) {
    throw new Error("VAPID public key not configured on server");
  }
  return publicKey;
}

/** What went wrong with the last thing the person asked for, in words for them. */
export type PushFailure = "turn-on" | "turn-off";

export interface PushNotificationState {
  /** What this browser can do; null until the page has opened in it. */
  device: DeviceFacts | null;
  permission: NotificationPermission | "unsupported";
  isSubscribed: boolean;
  /** The subscription has been checked against the signed-in user at least once. */
  checked: boolean;
  /** Turning on or off is under way, after the person asked for it. */
  busy: boolean;
  failure: PushFailure | null;
  /** Resolves true once this device is subscribed for the signed-in user. */
  subscribe: () => Promise<boolean>;
  /** Resolves true once this device is no longer subscribed. */
  unsubscribe: () => Promise<boolean>;
  /** Reads the browser's permission again, after the person changed it in its settings. */
  recheckPermission: () => void;
  /** Sends a test notification to the signed-in user's own devices. */
  sendTest: () => Promise<boolean>;
}

/** Push works in this browser as it is opened now (on iOS, only from the Home Screen). */
function canPush(device: DeviceFacts | null): device is DeviceFacts {
  return device !== null && device.pushSupported && !(device.ios && !device.standalone);
}

/**
 * This browser's notification subscription for the signed-in team member.
 * One instance serves every team page (see TeamDeviceProvider): two would
 * race each other over the same subscription.
 */
export function usePushNotifications(): PushNotificationState {
  const { getToken, isSignedIn, userId } = useAuth();
  const [device, setDevice] = useState<DeviceFacts | null>(null);
  const [permission, setPermission] = useState<
    NotificationPermission | "unsupported"
  >("unsupported");
  const [isSubscribed, setIsSubscribed] = useState(false);
  const [checked, setChecked] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<PushFailure | null>(null);
  const usable = canPush(device);
  // Clerk can transition directly between identities. Serialize subscription
  // ownership updates so an older user's request cannot finish last and take
  // the endpoint back from the current user.
  const identitySyncQueue = useRef<Promise<void>>(Promise.resolve());
  const enqueuePushOperation = useCallback((operation: () => Promise<void>) => {
    const task = identitySyncQueue.current
      .catch(() => undefined)
      .then(operation);
    // Keep the queue usable after an individual network failure while still
    // returning that failure to the caller that owns the UI state.
    identitySyncQueue.current = task.catch(() => undefined);
    return task;
  }, []);

  // The permission the page last saw. A different one means it was changed
  // in the browser's settings, which can end the subscription as well, so the
  // subscription is checked again (the effect below) and not assumed.
  const seenPermission = useRef<NotificationPermission | null>(null);
  const [permissionChanges, setPermissionChanges] = useState(0);
  const recheckPermission = useCallback(() => {
    if (typeof Notification === "undefined") return;
    const current = Notification.permission;
    if (seenPermission.current !== null && seenPermission.current !== current) {
      setPermissionChanges((count) => count + 1);
    }
    seenPermission.current = current;
    setPermission(current);
  }, []);

  // What the browser can do, read once the page is open in it. Whose the
  // subscription is gets settled in the identity-aware effect below, not
  // inferred from browser state.
  useEffect(() => {
    const facts = readDevice(navigator, window, window);
    setDevice(facts);
    if (!canPush(facts)) {
      // Nothing to check: it cannot be subscribed here.
      setChecked(true);
      return;
    }
    recheckPermission();
    // The permission changes outside the page, in the browser's settings;
    // coming back to the page is when to look again.
    const onVisible = () => {
      if (document.visibilityState === "visible") recheckPermission();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [recheckPermission]);

  // A PushSubscription belongs to the signed-in Clerk identity, not merely to
  // the browser profile. Re-assert that mapping on mount/account changes and
  // invalidate it locally on logout. Unknown or mismatched legacy ownership is
  // rotated instead of risking delivery of another user's private alerts.
  useEffect(() => {
    if (!usable) return;

    let cancelled = false;
    const updateSubscribed = (value: boolean) => {
      if (!cancelled) setIsSubscribed(value);
    };

    const reconcileIdentity = async () => {
      const registration = await navigator.serviceWorker.getRegistration(
        TEAM_SERVICE_WORKER_SCOPE,
      );
      let subscription =
        (await registration?.pushManager.getSubscription()) ?? null;
      const boundUserId = localStorage.getItem(PUSH_USER_STORAGE_KEY);

      if (!isSignedIn || !userId) {
        if (subscription) await subscription.unsubscribe();
        localStorage.removeItem(PUSH_USER_STORAGE_KEY);
        updateSubscribed(false);
        return;
      }

      if (!registration || Notification.permission !== "granted") {
        updateSubscribed(false);
        return;
      }

      if (subscription && boundUserId !== userId) {
        await subscription.unsubscribe();
        subscription = null;
        localStorage.removeItem(PUSH_USER_STORAGE_KEY);
      }

      if (!subscription) {
        updateSubscribed(false);
        return;
      }

      const token = await getToken();
      if (!token) throw new Error("Not signed in");
      // Existing permission and ownership are preserved, but the server may
      // have rotated its signing key since this browser last visited.
      subscription = await ensureCurrentPushSubscription(
        registration.pushManager,
        await fetchPublicKey(token),
      );
      const subJson = subscription.toJSON();
      const saveRes = await fetch(`${API_BASE}/push/subscribe`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          endpoint: subJson.endpoint,
          p256dh: subJson.keys?.p256dh,
          auth: subJson.keys?.auth,
        }),
      });
      if (!saveRes.ok) {
        throw new Error(
          `Failed to reconcile notification subscription (${saveRes.status})`,
        );
      }

      localStorage.setItem(PUSH_USER_STORAGE_KEY, userId);
      updateSubscribed(true);
    };

    const task = enqueuePushOperation(reconcileIdentity);
    void task
      .catch((err) => {
        console.error("Push identity sync error:", err);
        updateSubscribed(false);
      })
      .finally(() => {
        if (!cancelled) setChecked(true);
      });

    return () => {
      cancelled = true;
    };
  }, [enqueuePushOperation, getToken, isSignedIn, permissionChanges, usable, userId]);

  const subscribe = useCallback(async () => {
    if (!usable || busy || !isSignedIn || !userId) return false;
    setBusy(true);
    setFailure(null);

    try {
      // Asked first, straight from the click: iOS shows its prompt only while
      // handling a tap, and the queue below may wait on an earlier check.
      const perm = await Notification.requestPermission();
      // The page's own request, followed by its own subscription below: not
      // a change made behind its back.
      seenPermission.current = perm;
      setPermission(perm);
      if (perm !== "granted") return false;

      let subscribed = false;
      await enqueuePushOperation(async () => {
        // Register service worker
        const registration = await navigator.serviceWorker.register(
          TEAM_SERVICE_WORKER_URL,
          { scope: TEAM_SERVICE_WORKER_SCOPE },
        );
        await whenActive(registration);

        // Fetch VAPID public key
        const token = await getToken();
        if (!token) throw new Error("Not signed in");
        const publicKey = await fetchPublicKey(token);

        // Subscribe to push
        let subscription = await registration.pushManager.getSubscription();
        if (
          subscription &&
          localStorage.getItem(PUSH_USER_STORAGE_KEY) !== userId
        ) {
          await subscription.unsubscribe();
          subscription = null;
          localStorage.removeItem(PUSH_USER_STORAGE_KEY);
        }
        subscription = await ensureCurrentPushSubscription(
          registration.pushManager,
          publicKey,
        );

        const subJson = subscription.toJSON();

        // Send subscription to backend
        const saveRes = await fetch(`${API_BASE}/push/subscribe`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({
            endpoint: subJson.endpoint,
            p256dh: subJson.keys?.p256dh,
            auth: subJson.keys?.auth,
          }),
        });
        if (!saveRes.ok) {
          await subscription.unsubscribe();
          localStorage.removeItem(PUSH_USER_STORAGE_KEY);
          throw new Error(
            `Failed to save notification subscription (${saveRes.status})`,
          );
        }

        localStorage.setItem(PUSH_USER_STORAGE_KEY, userId);
        setIsSubscribed(true);
        subscribed = true;
      });
      return subscribed;
    } catch (err) {
      console.error("Push subscribe error:", err);
      setFailure("turn-on");
      return false;
    } finally {
      setBusy(false);
    }
  }, [busy, enqueuePushOperation, getToken, isSignedIn, usable, userId]);

  const unsubscribe = useCallback(async () => {
    if (!usable || busy) return false;
    setBusy(true);
    setFailure(null);

    try {
      await enqueuePushOperation(async () => {
        // The team's own registration, which need not control this page.
        const registration = await navigator.serviceWorker.getRegistration(
          TEAM_SERVICE_WORKER_SCOPE,
        );
        const subscription =
          (await registration?.pushManager.getSubscription()) ?? null;

        if (subscription) {
          const endpoint = subscription.endpoint;

          // Unsubscribe from browser push before contacting the backend so a
          // failed cleanup request still cannot deliver to this browser.
          await subscription.unsubscribe();
          localStorage.removeItem(PUSH_USER_STORAGE_KEY);
          setIsSubscribed(false);

          // Notify backend
          const token = await getToken();
          if (!token) return;
          const removeRes = await fetch(`${API_BASE}/push/unsubscribe`, {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              Authorization: `Bearer ${token}`,
            },
            body: JSON.stringify({ endpoint }),
          });
          if (!removeRes.ok) {
            throw new Error(
              `Failed to remove notification subscription (${removeRes.status})`,
            );
          }
        }

        localStorage.removeItem(PUSH_USER_STORAGE_KEY);
        setIsSubscribed(false);
      });
      return true;
    } catch (err) {
      console.error("Push unsubscribe error:", err);
      // The browser's own subscription ends before the API is told, so a
      // failure here can leave this device off already.
      const registration = await navigator.serviceWorker
        .getRegistration(TEAM_SERVICE_WORKER_SCOPE)
        .catch(() => undefined);
      const left = (await registration?.pushManager.getSubscription().catch(() => null)) ?? null;
      if (left) setFailure("turn-off");
      return left === null;
    } finally {
      setBusy(false);
    }
  }, [busy, enqueuePushOperation, getToken, usable]);

  const sendTest = useCallback(async () => {
    try {
      const token = await getToken();
      if (!token) return false;
      const response = await fetch(`${API_BASE}/push/test`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
      });
      return response.ok;
    } catch {
      return false;
    }
  }, [getToken]);

  return {
    device,
    permission,
    isSubscribed,
    checked,
    busy,
    failure,
    subscribe,
    unsubscribe,
    recheckPermission,
    sendTest,
  };
}
