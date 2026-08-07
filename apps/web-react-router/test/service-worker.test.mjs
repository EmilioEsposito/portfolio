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

await import("../public/sw.js");

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
      url: "https://example.test/sernia-chat?id=conversation-123",
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
      url: "https://example.test/sernia-chat?id=conversation-123",
    },
  ];

  await dispatchResponsePush();

  assert.equal(shownNotifications.length, 1);
  assert.equal(postedMessages.length, 0);
  assert.equal(shownNotifications[0].options.tag, "response-conversation-123");
});
