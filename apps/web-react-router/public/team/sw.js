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

// The app icon's badge counts this app's notifications still showing, that
// is, not yet opened; opening the app clears it (the team pages do that).
// The Badging API is in Home Screen apps on iOS 16.4+ and installed apps in
// desktop Chrome and Edge; elsewhere (Chrome on Android shows its own dot)
// this does nothing.
async function syncBadge() {
  try {
    const badging = self.navigator;
    if (!badging || typeof badging.setAppBadge !== "function") return;
    const showing = await self.registration.getNotifications();
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
  return false;
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
        data,
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
