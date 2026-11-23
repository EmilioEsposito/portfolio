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
