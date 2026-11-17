import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { refreshGmailWatch } from "../gmail-watch-refresh.mjs";

const env = { CUSTOM_RAILWAY_BACKEND_URL: "https://backend.test", CRON_SECRET: "private-test-secret" };
const success = () => Response.json({ success: true, expiration: String(Date.now() + 86_400_000), historyId: "123" });
function harness(replies) {
  const calls = [], waits = [], logs = [];
  return {
    calls, waits, logs,
    options: {
      env,
      fetchImpl: async (...args) => {
        calls.push(args);
        const next = replies.shift();
        if (next instanceof Error) throw next;
        return next;
      },
      wait: async ms => { waits.push(ms); },
      logger: { log: text => logs.push(text), error: text => logs.push(text) },
    },
  };
}

for (const status of [408, 429, 500, 503]) {
  test(`retries HTTP ${status} then reports actual renewal`, async () => {
    const h = harness([new Response("private error", { status }), success()]);
    await refreshGmailWatch(h.options);
    assert.equal(h.calls.length, 2);
    assert.deepEqual(h.waits, [2000]);
    assert.equal(h.calls[0][0].href, "https://backend.test/api/google/gmail/watch/refresh");
    assert.equal(h.calls[0][1].headers.Authorization, "Bearer private-test-secret");
    assert.equal(h.calls[0][1].method, "POST");
    assert.equal(h.calls[0][1].redirect, "error");
    assert.ok(h.calls[0][1].signal instanceof AbortSignal);
    assert.match(h.logs.at(-1), /renewed; expiration=/);
    assert.ok(!h.logs.join().includes("private"));
  });
}

test("network failure and timeout recover with bounded backoff", async () => {
  const h = harness([new TypeError("private URL"), new DOMException("timeout", "TimeoutError"), success()]);
  await refreshGmailWatch(h.options);
  assert.deepEqual(h.waits, [2000, 4000]);
  assert.ok(!h.logs.join().includes("private"));
});

for (const mode of ["http", "network"]) {
  test(`${mode} failure exhaustion rejects without success log`, async () => {
    const h = harness(Array.from({ length: 3 }, () => mode === "http" ? new Response("secret", { status: 503 }) : new Error("secret")));
    await assert.rejects(refreshGmailWatch(h.options), /failed/);
    assert.equal(h.calls.length, 3);
    assert.deepEqual(h.waits, [2000, 4000]);
    assert.ok(!h.logs.join().includes("renewed"));
    assert.ok(!h.logs.join().includes("secret"));
  });
}

for (const status of [400, 401, 403]) {
  test(`HTTP ${status} fails immediately`, async () => {
    const h = harness([new Response("private", { status })]);
    await assert.rejects(refreshGmailWatch(h.options), new RegExp(`HTTP ${status}`));
    assert.equal(h.calls.length, 1);
    assert.deepEqual(h.waits, []);
  });
}

for (const body of [{ success: false }, { success: true }, { success: true, expiration: "1", historyId: "123" }]) {
  test(`rejects unsuccessful or invalid renewal: ${JSON.stringify(body)}`, async () => {
    const h = harness([Response.json(body)]);
    await assert.rejects(refreshGmailWatch(h.options));
    assert.ok(!h.logs.join().includes("renewed"));
  });
}

test("malformed JSON fails", async () => {
  const h = harness([new Response("not JSON")]);
  await assert.rejects(refreshGmailWatch(h.options));
});

test("development skip is explicit, not a renewal", async () => {
  const h = harness([Response.json({ success: true, message: "Skipping Gmail watch refresh in hosted development environment" })]);
  await refreshGmailWatch(h.options);
  assert.match(h.logs.at(-1), /skipped/);
  assert.ok(!h.logs.join().includes("renewed"));
});

test("missing configuration fails before any network call", async () => {
  const h = harness([]);
  await assert.rejects(refreshGmailWatch({ ...h.options, env: {} }), /required/);
  assert.equal(h.calls.length, 0);
});

test("executable exits nonzero on failure", () => {
  const result = spawnSync(process.execPath, [new URL("../gmail-watch-refresh.mjs", import.meta.url).pathname], { env: {}, encoding: "utf8" });
  assert.equal(result.status, 1);
  assert.match(result.stderr, /exiting with status 1/);
  assert.ok(!result.stdout.includes("DONE"));
});

for (const status of [200, 401]) {
  test(`entrypoint exits ${status === 200 ? 0 : 1} for HTTP ${status}`, () => {
    const mock = status === 200
      ? 'globalThis.fetch = async () => Response.json({success:true,expiration:String(Date.now()+86400000),historyId:"123"});'
      : 'globalThis.fetch = async () => new Response("private error", {status:401});';
    const result = spawnSync(process.execPath, [
      "--import", `data:text/javascript,${encodeURIComponent(mock)}`,
      new URL("../gmail-watch-refresh.mjs", import.meta.url).pathname,
    ], { env, encoding: "utf8" });
    assert.equal(result.status, status === 200 ? 0 : 1);
    if (status === 200) assert.match(result.stdout, /renewed; expiration=/);
    else assert.match(result.stderr, /exiting with status 1/);
    assert.ok(!result.stdout.includes("private"));
  });
}
