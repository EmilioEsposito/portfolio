// Minimal service worker for Sernia Capital PWA push notifications.
// No caching — only handles push events and notification clicks.

self.addEventListener("push", (event) => {
  console.log("[sw.js] push event received", event.data?.text());

  if (!event.data) return;

  let payload;
  try {
    payload = event.data.json();
  } catch {
    payload = { title: "Sernia Capital", body: event.data.text() };
  }

  const { title = "Sernia Capital", body = "", data = {} } = payload;

  event.waitUntil(
    self.registration.showNotification(title, {
      body,
      icon: "/favicon.png",
      badge: "/favicon.png",
      tag: data.conversation_id || "sernia-default",
      data,
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();

  const url = event.notification.data?.url || "/sernia-chat";

  event.waitUntil(
    clients.matchAll({ type: "window", includeUncontrolled: true }).then((windowClients) => {
      // Focus an existing sernia-chat tab if one exists
      for (const client of windowClients) {
        if (client.url.includes("/sernia-chat") && "focus" in client) {
          client.navigate(url);
          return client.focus();
        }
      }
      // Otherwise open a new window
      return clients.openWindow(url);
    })
  );
});
