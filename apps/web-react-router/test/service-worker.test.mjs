import assert from "node:assert/strict";
import test from "node:test";

const handlers = {};
const shownNotifications = [];
const postedMessages = [];

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

// The app icon's badge: the number of this app's notifications still showing.

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
  self.registration.getNotifications = async () => showing();
  return badges;
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
  let showing = [{ tag: "approval-a" }, { tag: "alert-b" }];
  const badges = badging({ showing: () => showing });

  // The clicked one is closed before the count.
  showing = [{ tag: "alert-b" }];
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
