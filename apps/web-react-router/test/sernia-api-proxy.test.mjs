import assert from "node:assert/strict";
import test from "node:test";
import { serniaApiGuard } from "../server.js";

function guard(host, originalUrl) {
  const result = { forwarded: false, status: undefined, body: undefined };
  const req = { headers: host === undefined ? {} : { host }, originalUrl };
  const res = {
    status(code) { result.status = code; return this; },
    json(body) { result.body = body; return this; },
  };
  serniaApiGuard(req, res, () => { result.forwarded = true; });
  return result;
}

test("on a Sernia host the proxy answers 404 for anything but the public booking API and the team's API", () => {
  for (const url of ["/api/ai-demos/chat-emilio", "/api/docs", "/api/open_phone/contacts", "/api/sernia-ai/../docs", "/api"]) {
    const result = guard("serniacapital.com", url);
    assert.equal(result.forwarded, false, url);
    assert.equal(result.status, 404, url);
    assert.deepEqual(result.body, { detail: "Not Found" }, url);
  }
  for (const host of ["serniacapital.com", "www.serniacapital.com", "serniacapital.localhost:5293"]) {
    for (const url of [
      "/api/showing-bookings/public/units/320-03?t=abc",
      "/api/sernia-ai/chat",
      "/api/showing-bookings/calendar/callback?code=made-up&state=made-up",
      "/api/open_phone/tenants",
    ]) {
      const result = guard(host, url);
      assert.equal(result.forwarded, true, `${host}${url}`);
      assert.equal(result.status, undefined, `${host}${url}`);
    }
  }
});

test("on other hosts the proxy forwards every /api path, as before", () => {
  for (const host of ["eesposito.com", "localhost:5293", "react-router-portfolio-pr-123.up.railway.app", undefined]) {
    for (const url of ["/api/sernia-ai/chat", "/api/showing-bookings/public/units"]) {
      const result = guard(host, url);
      assert.equal(result.forwarded, true, `${host}${url}`);
      assert.equal(result.status, undefined, `${host}${url}`);
    }
  }
});
