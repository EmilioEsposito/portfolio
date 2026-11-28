import assert from "node:assert/strict";
import test from "node:test";

const handlers = {};
const shownNotifications = [];
const postedMessages = [];
// Chrome's push service unless a test says otherwise.
const FCM_ENDPOINT = "https://fcm.googleapis.com/fcm/send/abc";
let pushEndpoint = FCM_ENDPOINT;

globalThis.self = {
  addEventListener(name, handler) {
    handlers[name] = handler;
  },
  clients: {
    matchAll: async () => [],
    claim: async () => {},
  },
  registration: {
    async showNotification(title, options) {
      shownNotifications.push({ title, options });
    },
    pushManager: {
      async getSubscription() {
        return { endpoint: pushEndpoint };
      },
    },
  },
  skipWaiting: async () => {},
};
globalThis.clients = globalThis.self.clients;

await import("../public/team/sw.js");

async function dispatchResponsePush() {
  let completion;
  handlers.push({
    data: {
      json: () => ({
        title: "Sernia AI",
        body: "Your response is ready.",
        data: {
          type: "response",
          conversation_id: "conversation-123",
        },
      }),
    },
    waitUntil(promise) {
      completion = promise;
    },
  });
  await completion;
}

test("suppresses a response notification for the focused conversation", async () => {
  shownNotifications.length = 0;
  postedMessages.length = 0;
  self.clients.matchAll = async () => [
    {
      focused: true,
      url: "https://example.test/team/chat?id=conversation-123",
      postMessage(message) {
        postedMessages.push(message);
      },
    },
  ];

  await dispatchResponsePush();

  assert.equal(shownNotifications.length, 0);
  assert.deepEqual(postedMessages, [
    {
      type: "response-ready",
      data: {
        type: "response",
        conversation_id: "conversation-123",
      },
    },
  ]);
});

test("on Apple's push service, shows the notification even for the focused conversation", async () => {
  // WebKit deletes the subscription after three pushes that showed nothing.
  shownNotifications.length = 0;
  postedMessages.length = 0;
  pushEndpoint = "https://web.push.apple.com/QGz9fPjX";
  self.clients.matchAll = async () => [
    {
      focused: true,
      url: "https://example.test/team/chat?id=conversation-123",
      postMessage(message) {
        postedMessages.push(message);
      },
    },
  ];

  try {
    await dispatchResponsePush();
  } finally {
    pushEndpoint = FCM_ENDPOINT;
  }

  assert.equal(shownNotifications.length, 1);
  // The open page still gets the completion signal.
  assert.equal(postedMessages.length, 1);
  assert.equal(postedMessages[0].type, "response-ready");
});

test("shows a response notification when the conversation is not focused", async () => {
  shownNotifications.length = 0;
  postedMessages.length = 0;
  self.clients.matchAll = async () => [
    {
      focused: false,
      url: "https://example.test/team/chat?id=conversation-123",
    },
  ];

  await dispatchResponsePush();

  assert.equal(shownNotifications.length, 1);
  assert.equal(postedMessages.length, 0);
  assert.equal(shownNotifications[0].options.tag, "response-conversation-123");
});

test("does not treat another page as the open conversation", async () => {
  shownNotifications.length = 0;
  postedMessages.length = 0;
  self.clients.matchAll = async () => [
    {
      focused: true,
      url: "https://example.test/sernia-chat?id=conversation-123",
      postMessage(message) {
        postedMessages.push(message);
      },
    },
  ];

  await dispatchResponsePush();

  assert.equal(shownNotifications.length, 1);
  assert.equal(postedMessages.length, 0);
});

async function clickNotification(data, windowClients) {
  const opened = [];
  let completion;
  globalThis.clients.matchAll = async () => windowClients;
  globalThis.clients.openWindow = async (url) => {
    opened.push(url);
  };
  handlers.notificationclick({
    notification: { data, close() {} },
    waitUntil(promise) {
      completion = promise;
    },
  });
  await completion;
  return opened;
}

test("a click opens the team chat when no chat tab is open", async () => {
  const opened = await clickNotification({ conversation_id: "conversation-123" }, [
    { url: "https://example.test/team/leads/bookings", focus: async () => {} },
  ]);
  assert.deepEqual(opened, ["/team/chat"]);
});

test("a click moves an open team chat tab to the notification's conversation", async () => {
  const events = [];
  const tab = {
    url: "https://example.test/team/chat?id=other",
    postMessage(message) {
      events.push(["message", message.type, message.url]);
    },
    navigate(url) {
      events.push(["navigate", url]);
    },
    focus: async () => {
      events.push(["focus"]);
    },
  };
  const opened = await clickNotification({ url: "/team/chat?id=conversation-123", conversation_id: "conversation-123" }, [tab]);
  assert.deepEqual(opened, []);
  assert.deepEqual(events, [
    ["message", "notification-click", "/team/chat?id=conversation-123"],
    ["navigate", "/team/chat?id=conversation-123"],
    ["focus"],
  ]);
});

test("a lead's notification moves an open chat tab to the lead's page, with nothing posted to the chat", async () => {
  const events = [];
  const tab = {
    url: "https://example.test/team/chat?id=other",
    postMessage(message) {
      events.push(["message", message.type, message.url]);
    },
    navigate(url) {
      events.push(["navigate", url]);
    },
    focus: async () => {
      events.push(["focus"]);
    },
  };
  const opened = await clickNotification(
    { url: "/team/leads/threads/1a2b3c4d5e6f7a8b", conversation_id: "zillow-1-2", type: "approval" },
    [tab],
  );
  assert.deepEqual(opened, []);
  assert.deepEqual(events, [["navigate", "/team/leads/threads/1a2b3c4d5e6f7a8b"], ["focus"]]);
});

test("a lead's notification opens the lead's page when no chat tab is open", async () => {
  const opened = await clickNotification({ url: "/team/leads/threads/1a2b3c4d5e6f7a8b", conversation_id: "zillow-1-2" }, []);
  assert.deepEqual(opened, ["/team/leads/threads/1a2b3c4d5e6f7a8b"]);
});

// The app icon's badge: the notifications shown since the app was last opened
// and still showing.

function badging({ showing }) {
  const badges = [];
  self.navigator = {
    async setAppBadge(count) {
      badges.push(count);
    },
    async clearAppBadge() {
      badges.push("cleared");
    },
  };
  // The browser hands back notifications with the data they were shown with.
  self.registration.getNotifications = async () =>
    showing().map((shown) => (shown.options ? { data: shown.options.data } : shown));
  return badges;
}

/** Cache Storage, which is where the worker keeps when the app was last opened. */
function withStore() {
  const kept = new Map();
  globalThis.caches = {
    open: async () => ({
      match: async (key) => (kept.has(key) ? new Response(kept.get(key)) : undefined),
      put: async (key, response) => void kept.set(key, await response.text()),
    }),
  };
  return () => delete globalThis.caches;
}

async function appOpened() {
  let completion;
  handlers.message({
    data: { type: "app-opened" },
    waitUntil(promise) {
      completion = promise;
    },
  });
  await completion;
}

function withoutBadging() {
  delete self.navigator;
  delete self.registration.getNotifications;
}

test("a notification shown sets the app badge to the number still showing", async () => {
  shownNotifications.length = 0;
  postedMessages.length = 0;
  const badges = badging({ showing: () => shownNotifications });
  self.clients.matchAll = async () => [];

  await dispatchResponsePush();
  await dispatchResponsePush();

  assert.equal(shownNotifications.length, 2);
  assert.deepEqual(badges, [1, 2]);
  withoutBadging();
});

test("a notification suppressed for the focused conversation sets no badge", async () => {
  shownNotifications.length = 0;
  postedMessages.length = 0;
  const badges = badging({ showing: () => shownNotifications });
  self.clients.matchAll = async () => [
    {
      focused: true,
      url: "https://example.test/team/chat?id=conversation-123",
      postMessage(message) {
        postedMessages.push(message);
      },
    },
  ];

  await dispatchResponsePush();

  assert.equal(shownNotifications.length, 0);
  assert.equal(postedMessages.length, 1);
  assert.deepEqual(badges, []);
  withoutBadging();
});

test("a click, or a notification put away, counts again what is still showing", async () => {
  let showing = [
    { tag: "approval-a", data: { shownAt: 1 } },
    { tag: "alert-b", data: { shownAt: 2 } },
  ];
  const badges = badging({ showing: () => showing });

  // The clicked one is closed before the count.
  showing = [{ tag: "alert-b", data: { shownAt: 2 } }];
  await clickNotification({ conversation_id: "conversation-123" }, []);
  assert.deepEqual(badges, [1]);

  showing = [];
  let completion;
  handlers.notificationclose({
    notification: { data: {} },
    waitUntil(promise) {
      completion = promise;
    },
  });
  await completion;
  assert.deepEqual(badges, [1, "cleared"]);
  withoutBadging();
});

test("opening the app stops the notifications shown until then from counting", async () => {
  shownNotifications.length = 0;
  const badges = badging({ showing: () => shownNotifications });
  const withoutStore = withStore();
  self.clients.matchAll = async () => [];

  await dispatchResponsePush();
  await dispatchResponsePush();
  assert.deepEqual(badges, [1, 2]);

  // The app is opened. Its two notifications stay in the notification
  // centre, and the badge goes.
  await appOpened();
  assert.deepEqual(badges, [1, 2, "cleared"]);

  // The next one counts alone, not as a third.
  await new Promise((resolve) => setTimeout(resolve, 5));
  await dispatchResponsePush();
  assert.equal(shownNotifications.length, 3);
  assert.deepEqual(badges, [1, 2, "cleared", 1]);

  // Putting an old one away does not bring the other old one back.
  shownNotifications.shift();
  let completion;
  handlers.notificationclose({
    notification: { data: {} },
    waitUntil(promise) {
      completion = promise;
    },
  });
  await completion;
  assert.deepEqual(badges, [1, 2, "cleared", 1, 1]);

  // Any other message leaves the count alone.
  handlers.message({ data: { type: "something-else" }, waitUntil() {} });
  assert.equal(badges.length, 5);
  withoutStore();
  withoutBadging();
});

test("a notification shown by an older worker, with no time on it, never counts", async () => {
  const badges = badging({ showing: () => [{ tag: "old", data: {} }, { tag: "older" }] });
  let completion;
  handlers.notificationclose({
    notification: { data: {} },
    waitUntil(promise) {
      completion = promise;
    },
  });
  await completion;
  assert.deepEqual(badges, ["cleared"]);
  withoutBadging();
});

test("a browser without the Badging API, or one that refuses it, still shows the notification", async () => {
  withoutBadging();
  shownNotifications.length = 0;
  self.clients.matchAll = async () => [];
  await dispatchResponsePush();
  assert.equal(shownNotifications.length, 1);
  const opened = await clickNotification({ conversation_id: "conversation-123" }, []);
  assert.deepEqual(opened, ["/team/chat"]);

  // An API that is there but refuses (an iPhone's browser tab, a denied permission).
  self.navigator = {
    async setAppBadge() {
      throw new DOMException("Not allowed", "NotAllowedError");
    },
  };
  self.registration.getNotifications = async () => shownNotifications;
  await dispatchResponsePush();
  assert.equal(shownNotifications.length, 2);
  withoutBadging();
});
