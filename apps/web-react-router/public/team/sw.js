// Minimal service worker for the Sernia team's pages (scope /team/): push
// notifications and the app icon's badge only, no caching. The team pages
// register it from /team/sw.js, so it controls nothing outside /team/.

const CHAT_PATH = "/team/chat";

self.addEventListener("install", (event) => {
  event.waitUntil(self.skipWaiting());
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

// The app icon's badge counts the notifications shown since the app was last
// opened and still showing. Opening the app does not close its notifications,
// so the moment it was opened is kept (a team page says so, see "message"
// below) and only notifications shown after it count. Without that, putting
// one old notification away, or the next push, would bring the others back
// into the count.
// The Badging API is in Home Screen apps on iOS 16.4+ and installed apps in
// desktop Chrome and Edge; elsewhere (Chrome on Android shows its own dot)
// this does nothing.
const BADGE_STATE = "sernia-team-badge";
const OPENED_AT = "/team/__app-opened-at";

// Kept in Cache Storage, the one store a worker that keeps no pages has at
// hand: the worker itself is stopped and restarted between pushes.
async function appOpenedAt() {
  try {
    const kept = await (await caches.open(BADGE_STATE)).match(OPENED_AT);
    return kept ? Number(await kept.text()) || 0 : 0;
  } catch {
    return 0;
  }
}

async function rememberAppOpened() {
  try {
    await (await caches.open(BADGE_STATE)).put(OPENED_AT, new Response(String(Date.now())));
  } catch {
    // Without the store every notification still showing counts, as before.
  }
}

async function syncBadge() {
  try {
    const badging = self.navigator;
    if (!badging || typeof badging.setAppBadge !== "function") return;
    const openedAt = await appOpenedAt();
    const showing = (await self.registration.getNotifications()).filter(
      (notification) => (notification.data?.shownAt ?? 0) > openedAt
    );
    if (showing.length > 0) {
      await badging.setAppBadge(showing.length);
    } else if (typeof badging.clearAppBadge === "function") {
      await badging.clearAppBadge();
    } else {
      await badging.setAppBadge(0);
    }
  } catch {
    // No badge here, or the browser refused one: nothing to show.
  }
}

// Apple's push service (Home Screen apps on iPhone and iPad, Safari on a Mac)
// deletes every push subscription of a site after three pushes that showed no
// notification, and never forgives one (WebKit's maxSilentPushCount). There a
// push is always shown. The API already sends no push about a conversation to
// someone who has it open (push/presence.py); this only catches the rare push
// that crosses a reader arriving at the page.
async function mayStaySilent() {
  try {
    const subscription = await self.registration.pushManager.getSubscription();
    if (!subscription) return false;
    return !new URL(subscription.endpoint).hostname.endsWith("push.apple.com");
  } catch {
    return false;
  }
}

async function shouldShowNotification(data) {
  if (data.type !== "response") return true;

  const windowClients = await self.clients.matchAll({
    type: "window",
    includeUncontrolled: true,
  });

  const focusedConversation = windowClients.find((client) => {
    if (!client.focused) return false;

    try {
      const clientUrl = new URL(client.url);
      return (
        clientUrl.pathname === CHAT_PATH &&
        clientUrl.searchParams.get("id") === data.conversation_id
      );
    } catch {
      return false;
    }
  });

  if (!focusedConversation) return true;

  // Suppressing the system notification must not suppress the completion
  // signal itself. The focused page uses this to replace a stuck/lost stream
  // with the newly persisted authoritative response.
  focusedConversation.postMessage({
    type: "response-ready",
    data,
  });
  return !(await mayStaySilent());
}

self.addEventListener("push", (event) => {
  if (!event.data) return;

  let payload;
  try {
    payload = event.data.json();
  } catch {
    payload = { title: "Sernia Capital", body: event.data.text() };
  }

  const { title = "Sernia Capital", body = "", data = {} } = payload;

  // Approval notifications persist until acted on; alerts auto-dismiss.
  const isApproval = data.type === "approval";
  const notificationType = isApproval
    ? "approval"
    : data.type === "response"
      ? "response"
      : "alert";
  const tag = `${notificationType}-${data.conversation_id || "sernia-default"}`;

  event.waitUntil(
    (async () => {
      if (!(await shouldShowNotification(data))) return;

      await self.registration.showNotification(title, {
        body,
        icon: "/favicon.png",
        badge: "/favicon.png",
        tag,
        // When it was shown, for the badge: see syncBadge.
        data: { ...data, shownAt: Date.now() },
        requireInteraction: isApproval,
      });
      await syncBadge();
    })()
  );
});

function isChat(url) {
  try {
    return new URL(url).pathname === CHAT_PATH;
  } catch {
    return false;
  }
}

self.addEventListener("notificationclick", (event) => {
  event.notification.close();

  const url = event.notification.data?.url || CHAT_PATH;

  event.waitUntil(
    Promise.all([
      clients.matchAll({ type: "window", includeUncontrolled: true }).then((windowClients) => {
        // Focus an existing chat tab if one exists.
        for (const client of windowClients) {
          if (isChat(client.url) && "focus" in client) {
            // Tell the page to refresh conversation data before navigating.
            client.postMessage({
              type: "notification-click",
              url,
              data: event.notification.data,
            });
            client.navigate(url);
            return client.focus();
          }
        }
        return clients.openWindow(url);
      }),
      // The one clicked is closed above; count the rest.
      syncBadge(),
    ])
  );
});

// Dismissed without opening it (where the browser says so): count the rest.
self.addEventListener("notificationclose", (event) => {
  event.waitUntil(syncBadge());
});

// A team page is open and visible: what was shown until now has been seen.
self.addEventListener("message", (event) => {
  if (event.data?.type !== "app-opened") return;
  event.waitUntil(rememberAppOpened().then(syncBadge));
});
