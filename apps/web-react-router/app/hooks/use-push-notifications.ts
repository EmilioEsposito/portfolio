import { useState, useEffect, useCallback } from "react";
import { useAuth } from "@clerk/react-router";

const API_BASE = "/api/sernia-ai";

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

interface PushNotificationState {
  isSupported: boolean;
  permission: NotificationPermission | "unsupported";
  isSubscribed: boolean;
  isLoading: boolean;
  needsInstall: boolean;
  subscribe: () => Promise<void>;
  unsubscribe: () => Promise<void>;
}

export function usePushNotifications(): PushNotificationState {
  const { getToken } = useAuth();
  const [isSupported, setIsSupported] = useState(false);
  const [permission, setPermission] = useState<NotificationPermission | "unsupported">("unsupported");
  const [isSubscribed, setIsSubscribed] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [needsInstall, setNeedsInstall] = useState(false);

  // Check support and current state on mount
  useEffect(() => {
    if (typeof window === "undefined") return;

    const supported = "serviceWorker" in navigator && "PushManager" in window;
    setIsSupported(supported);

    if (!supported) return;

    // iOS detection: Web Push requires standalone mode (Add to Home Screen)
    const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent);
    const isStandalone =
      window.matchMedia("(display-mode: standalone)").matches ||
      ("standalone" in navigator && (navigator as { standalone?: boolean }).standalone === true);

    if (isIOS && !isStandalone) {
      setNeedsInstall(true);
      return;
    }

    setPermission(Notification.permission);

    // Check if already subscribed
    navigator.serviceWorker.ready.then((registration) => {
      registration.pushManager.getSubscription().then((sub) => {
        setIsSubscribed(sub !== null);
      });
    });
  }, []);

  const subscribe = useCallback(async () => {
    if (!isSupported || isLoading) return;
    setIsLoading(true);

    try {
      // Register service worker
      const registration = await navigator.serviceWorker.register("/sw.js");
      await navigator.serviceWorker.ready;

      // Request notification permission
      const perm = await Notification.requestPermission();
      setPermission(perm);
      if (perm !== "granted") {
        setIsLoading(false);
        return;
      }

      // Fetch VAPID public key
      const token = await getToken();
      const vapidRes = await fetch(`${API_BASE}/push/vapid-public-key`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      const { publicKey } = await vapidRes.json();

      if (!publicKey) {
        console.error("VAPID public key not configured on server");
        setIsLoading(false);
        return;
      }

      // Subscribe to push
      const subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(publicKey).buffer as ArrayBuffer,
      });

      const subJson = subscription.toJSON();

      // Send subscription to backend
      await fetch(`${API_BASE}/push/subscribe`, {
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

      setIsSubscribed(true);
    } catch (err) {
      console.error("Push subscribe error:", err);
    } finally {
      setIsLoading(false);
    }
  }, [isSupported, isLoading, getToken]);

  const unsubscribe = useCallback(async () => {
    if (!isSupported || isLoading) return;
    setIsLoading(true);

    try {
      const registration = await navigator.serviceWorker.ready;
      const subscription = await registration.pushManager.getSubscription();

      if (subscription) {
        const endpoint = subscription.endpoint;

        // Unsubscribe from browser push
        await subscription.unsubscribe();

        // Notify backend
        const token = await getToken();
        await fetch(`${API_BASE}/push/unsubscribe`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({ endpoint }),
        });
      }

      setIsSubscribed(false);
    } catch (err) {
      console.error("Push unsubscribe error:", err);
    } finally {
      setIsLoading(false);
    }
  }, [isSupported, isLoading, getToken]);

  return {
    isSupported,
    permission,
    isSubscribed,
    isLoading,
    needsInstall,
    subscribe,
    unsubscribe,
  };
}
