import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";
import { pathForLog, proxyToBackend } from "../server.js";

// Google's callback brings a one-time code and a signed state in its query.
const PATH = "/api/showing-bookings/calendar/callback";
const QUERY = "?code=4%2Fsecret-code-0001&state=secret-state.0002";
const TARGET = `https://backend.test${PATH}${QUERY}`;
const SECRETS = ["secret-code", "4/secret", "secret-state"];

function request(url, method = "GET") {
  return Object.assign(new EventEmitter(), { method, originalUrl: url, headers: { host: "eesposito.com" } });
}

function response() {
  return Object.assign(new EventEmitter(), {
    destroyed: false,
    writableEnded: false,
    headersSent: false,
    statusCode: 200,
    body: undefined,
    setHeader() {},
    status(code) {
      this.statusCode = code;
      return this;
    },
    json(body) {
      this.body = body;
      this.writableEnded = true;
      return this;
    },
    flushHeaders() {
      this.headersSent = true;
    },
    write() {
      return true;
    },
    end() {
      this.writableEnded = true;
    },
  });
}

/** Everything the proxy writes to the console during the test. */
function capture(t) {
  const lines = [];
  const record = (...args) =>
    lines.push(args.map((arg) => (arg instanceof Error ? `${arg.stack} ${arg.cause?.stack ?? ""}` : String(arg))).join(" "));
  t.mock.method(console, "log", record);
  t.mock.method(console, "error", record);
  return lines;
}

test("a proxied request logs its method and path, never its query, and forwards the query whole", async (t) => {
  const lines = capture(t);
  let forwarded;
  t.mock.method(globalThis, "fetch", async (url) => {
    forwarded = url;
    return new Response("<!doctype html>", { status: 200 });
  });

  const res = response();
  await proxyToBackend(request(`${PATH}${QUERY}`), res, TARGET, "API Proxy");

  assert.equal(forwarded, TARGET);
  assert.equal(res.statusCode, 200);
  assert.deepEqual(lines, [`[API Proxy] GET ${PATH}`]);
});

test("a proxy error logs neither the code nor the state, even when the error quotes the address", async (t) => {
  const lines = capture(t);
  t.mock.method(globalThis, "fetch", async (url) => {
    throw new TypeError(`Failed to parse URL from ${url}`, {
      cause: Object.assign(new Error(`connect ECONNREFUSED for ${decodeURIComponent(String(url))}`), {
        code: "ECONNREFUSED",
      }),
    });
  });

  const res = response();
  await proxyToBackend(request(`${PATH}${QUERY}`), res, TARGET, "API Proxy");

  assert.equal(res.statusCode, 502);
  const logged = lines.join("\n");
  assert.match(logged, /Error proxying GET \/api\/showing-bookings\/calendar\/callback: TypeError/);
  assert.match(logged, /ECONNREFUSED/);
  for (const secret of SECRETS) assert.ok(!logged.includes(secret), secret);
});

test("every proxied path is logged without its query, not only the callback", () => {
  assert.equal(pathForLog("/api/showing-bookings/notices?limit=5"), "/api/showing-bookings/notices");
  assert.equal(pathForLog("/api/sernia-ai/chat#frag"), "/api/sernia-ai/chat");
  assert.equal(pathForLog("/api/health"), "/api/health");
});
