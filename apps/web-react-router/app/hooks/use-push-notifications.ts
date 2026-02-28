import { useState, useEffect, useCallback, useRef } from "react";
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
  const { getToken } = useAuth();
  const [isSupported, setIsSupported] = useState(false);
  const [permission, setPermission] = useState<NotificationPermission | "unsupported">("unsupported");
  const [isSubscribed, setIsSubscribed] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [needsInstall, setNeedsInstall] = useState(false);
  const [iosBrowser, setIosBrowser] = useState<"safari" | "chrome" | null>(null);
  const [canInstall, setCanInstall] = useState(false);
  const deferredInstallPrompt = useRef<BeforeInstallPromptEvent | null>(null);

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
      // CriOS = Chrome on iOS, everything else on iOS is Safari WebKit
      setIosBrowser(/CriOS/.test(navigator.userAgent) ? "chrome" : "safari");
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
