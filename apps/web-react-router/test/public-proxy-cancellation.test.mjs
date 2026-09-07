import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";
import { proxyToBackend } from "../server.js";

function request(path) {
  return Object.assign(new EventEmitter(), { method: "POST", originalUrl: path, headers: {} });
}
function response() {
  return Object.assign(new EventEmitter(), {
    destroyed: false, writableEnded: false, headersSent: false,
    setHeader() {}, status() { return this; },
    flushHeaders() { this.headersSent = true; },
    write() { return true; }, end() { this.writableEnded = true; },
    disconnect() { this.destroyed = true; this.emit("close"); },
  });
}

for (const path of [
  "/api/ai-demos/chat-emilio", "/api/ai-demos/chat-weather",
  "/api/ai-demos/multi-agent-chat/?qa=1", "/api/google/gmail/generate_email_response",
]) {
  test(`browser disconnect cancels public stream: ${path}`, async (t) => {
    let cancelled = false;
    let signal;
    let started;
    const ready = new Promise((resolve) => { started = resolve; });
    t.mock.method(globalThis, "fetch", async (_url, options) => {
      signal = options.signal;
      return new Response(new ReadableStream({
        pull() { started(); },
        cancel() { cancelled = true; },
      }));
    });
    const req = request(path);
    const res = response();
    const proxy = proxyToBackend(req, res, "https://backend.test", "test");
    await ready;
    res.disconnect();
    await proxy;
    assert.equal(signal.aborted, true);
    assert.equal(cancelled, true);
    assert.equal(res.listenerCount("close"), 0);
    assert.equal(res.listenerCount("error"), 0);
    assert.equal(req.listenerCount("aborted"), 0);
  });
}

test("public disconnect aborts inference before upstream headers arrive", async (t) => {
  let started;
  const ready = new Promise((resolve) => { started = resolve; });
  let signal;
  t.mock.method(globalThis, "fetch", (_url, options) => {
    signal = options.signal;
    started();
    return new Promise((_resolve, reject) => {
      signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
    });
  });
  const req = request("/api/ai-demos/multi-agent-chat");
  const res = response();
  const proxy = proxyToBackend(req, res, "https://backend.test", "test");
  await ready;
  res.disconnect();
  await proxy;
  assert.equal(signal.aborted, true);
  assert.equal(res.listenerCount("close"), 0);
});

test("operational conversations still drain upstream after browser disconnect", async (t) => {
  let upstream;
  let cancelled = false;
  let signal;
  t.mock.method(globalThis, "fetch", async (_url, options) => {
    signal = options.signal;
    return new Response(new ReadableStream({
      start(controller) { upstream = controller; },
      cancel() { cancelled = true; },
    }));
  });
  const res = response();
  const proxy = proxyToBackend(request("/api/sernia-ai/chat"), res, "https://backend.test", "test");
  res.disconnect();
  upstream.enqueue(new TextEncoder().encode("persist me"));
  upstream.close();
  await proxy;
  assert.equal(signal, undefined);
  assert.equal(cancelled, false);
});
