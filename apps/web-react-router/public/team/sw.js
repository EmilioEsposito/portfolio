// Minimal service worker for Sernia Capital PWA push notifications.
// No caching — only handles worker lifecycle and push notifications.

self.addEventListener("install", (event) => {
  event.waitUntil(self.skipWaiting());
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

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
        clientUrl.pathname === "/sernia-chat" &&
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
    })()
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();

  const url = event.notification.data?.url || "/sernia-chat";

  event.waitUntil(
    clients.matchAll({ type: "window", includeUncontrolled: true }).then((windowClients) => {
      // Focus an existing sernia-chat tab if one exists.
      for (const client of windowClients) {
        if (client.url.includes("/sernia-chat") && "focus" in client) {
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
    })
  );
});
