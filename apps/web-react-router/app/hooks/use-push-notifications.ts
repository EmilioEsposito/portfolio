import { useState, useEffect, useCallback, useRef } from "react";
import { useAuth } from "@clerk/react-router";

const API_BASE = "/api/sernia-ai";
const PUSH_USER_STORAGE_KEY = "sernia-push-user-id";

function urlBase64ToUint8Array(base64String: string): Uint8Array {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  const output = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) {
    output[i] = raw.charCodeAt(i);
  }
  return output;
}

interface BeforeInstallPromptEvent extends Event {
  prompt(): Promise<{ outcome: "accepted" | "dismissed" }>;
}

interface PushNotificationState {
  isSupported: boolean;
  permission: NotificationPermission | "unsupported";
  isSubscribed: boolean;
  isLoading: boolean;
  needsInstall: boolean;
  /** "safari" | "chrome" | null — which iOS browser, for install instructions */
  iosBrowser: "safari" | "chrome" | null;
  /** True when push is available but user hasn't subscribed yet — show a prompt */
  shouldPrompt: boolean;
  /** True when the browser offers a PWA install prompt (Android Chrome) */
  canInstall: boolean;
  subscribe: () => Promise<void>;
  unsubscribe: () => Promise<void>;
  /** Trigger the browser's native PWA install prompt (Android Chrome) */
  promptInstall: () => Promise<void>;
}

export function usePushNotifications(): PushNotificationState {
  const { getToken, isSignedIn, userId } = useAuth();
  const [isSupported, setIsSupported] = useState(false);
  const [permission, setPermission] = useState<
    NotificationPermission | "unsupported"
  >("unsupported");
  const [isSubscribed, setIsSubscribed] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [needsInstall, setNeedsInstall] = useState(false);
  const [iosBrowser, setIosBrowser] = useState<"safari" | "chrome" | null>(
    null,
  );
  const [canInstall, setCanInstall] = useState(false);
  const deferredInstallPrompt = useRef<BeforeInstallPromptEvent | null>(null);
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

  // Capture the beforeinstallprompt event (Android Chrome / desktop Chrome)
  useEffect(() => {
    if (typeof window === "undefined") return;

    const handler = (e: Event) => {
      e.preventDefault(); // Prevent Chrome's default mini-infobar
      deferredInstallPrompt.current = e as BeforeInstallPromptEvent;
      setCanInstall(true);
    };

    window.addEventListener("beforeinstallprompt", handler);
    return () => window.removeEventListener("beforeinstallprompt", handler);
  }, []);

  // Check platform support on mount. Subscription ownership is reconciled in
  // the identity-aware effect below rather than inferred from browser state.
  useEffect(() => {
    if (typeof window === "undefined") return;

    const supported = "serviceWorker" in navigator && "PushManager" in window;
    setIsSupported(supported);

    if (!supported) return;

    // iOS detection: Web Push requires standalone mode (Add to Home Screen)
    const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent);
    const isStandalone =
      window.matchMedia("(display-mode: standalone)").matches ||
      ("standalone" in navigator &&
        (navigator as { standalone?: boolean }).standalone === true);

    if (isIOS && !isStandalone) {
      setNeedsInstall(true);
      // CriOS = Chrome on iOS, everything else on iOS is Safari WebKit
      setIosBrowser(/CriOS/.test(navigator.userAgent) ? "chrome" : "safari");
      return;
    }

    setPermission(Notification.permission);
  }, []);

  // A PushSubscription belongs to the signed-in Clerk identity, not merely to
  // the browser profile. Re-assert that mapping on mount/account changes and
  // invalidate it locally on logout. Unknown or mismatched legacy ownership is
  // rotated instead of risking delivery of another user's private alerts.
  useEffect(() => {
    if (!isSupported || needsInstall) return;

    let cancelled = false;
    setIsLoading(true);
    const updateSubscribed = (value: boolean) => {
      if (!cancelled) setIsSubscribed(value);
    };

    const reconcileIdentity = async () => {
      const registration = await navigator.serviceWorker.getRegistration();
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
        if (!cancelled) setIsLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [
    enqueuePushOperation,
    getToken,
    isSignedIn,
    isSupported,
    needsInstall,
    userId,
  ]);

  const subscribe = useCallback(async () => {
    if (!isSupported || isLoading || !isSignedIn || !userId) return;
    setIsLoading(true);

    try {
      await enqueuePushOperation(async () => {
        // Register service worker
        const registration = await navigator.serviceWorker.register("/sw.js");
        await navigator.serviceWorker.ready;

        // Request notification permission
        const perm = await Notification.requestPermission();
        setPermission(perm);
        if (perm !== "granted") return;

        // Fetch VAPID public key
        const token = await getToken();
        if (!token) throw new Error("Not signed in");
        const vapidRes = await fetch(`${API_BASE}/push/vapid-public-key`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (!vapidRes.ok) {
          throw new Error(
            `Failed to load notification key (${vapidRes.status})`,
          );
        }
        const { publicKey } = await vapidRes.json();

        if (!publicKey) {
          throw new Error("VAPID public key not configured on server");
        }

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
        subscription =
          subscription ||
          (await registration.pushManager.subscribe({
            userVisibleOnly: true,
            applicationServerKey: urlBase64ToUint8Array(publicKey)
              .buffer as ArrayBuffer,
          }));

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
      });
    } catch (err) {
      console.error("Push subscribe error:", err);
    } finally {
      setIsLoading(false);
    }
  }, [
    enqueuePushOperation,
    getToken,
    isLoading,
    isSignedIn,
    isSupported,
    userId,
  ]);

  const unsubscribe = useCallback(async () => {
    if (!isSupported || isLoading) return;
    setIsLoading(true);

    try {
      await enqueuePushOperation(async () => {
        const registration = await navigator.serviceWorker.ready;
        const subscription = await registration.pushManager.getSubscription();

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
    } catch (err) {
      console.error("Push unsubscribe error:", err);
    } finally {
      setIsLoading(false);
    }
  }, [enqueuePushOperation, getToken, isLoading, isSupported]);

  const promptInstall = useCallback(async () => {
    if (!deferredInstallPrompt.current) return;
    const { outcome } = await deferredInstallPrompt.current.prompt();
    if (outcome === "accepted") {
      setCanInstall(false);
      deferredInstallPrompt.current = null;
    }
  }, []);

  // Show a prompt when push is available but user hasn't opted in yet.
  // On desktop/Android: supported + not subscribed + permission not yet denied
  // On iOS standalone: same (needsInstall is false once installed)
  const shouldPrompt =
    isSupported && !needsInstall && !isSubscribed && permission !== "denied";

  return {
    isSupported,
    permission,
    isSubscribed,
    isLoading,
    needsInstall,
    iosBrowser,
    shouldPrompt,
    canInstall,
    subscribe,
    unsubscribe,
    promptInstall,
  };
}
