import assert from "node:assert/strict";
import test from "node:test";

import {
  serverHasResponseForRequest,
  userMessageFingerprint,
} from "./sernia-chat-recovery.ts";

const userMessage = (text: string) => ({
  role: "user",
  parts: [{ type: "text", text }],
});

const assistantMessage = (text: string) => ({
  role: "assistant",
  parts: [{ type: "text", text }],
});

test("fingerprints do not retain raw prompt content", async () => {
  const fingerprint = await userMessageFingerprint(
    userMessage("private tenant details"),
  );

  assert.equal(fingerprint.length, 64);
  assert.equal(fingerprint.includes("private tenant details"), false);
});

test("rejects a stale snapshot that predates the submitted turn", async () => {
  const marker = {
    fingerprint: await userMessageFingerprint(userMessage("latest request")),
    occurrence: 1,
  };
  const staleSnapshot = [
    userMessage("older request"),
    assistantMessage("older response"),
  ];

  assert.equal(await serverHasResponseForRequest(staleSnapshot, marker), false);
});

test("accepts the submitted turn only after an assistant response is persisted", async () => {
  const submitted = userMessage("latest request");
  const marker = {
    fingerprint: await userMessageFingerprint(submitted),
    occurrence: 1,
  };

  assert.equal(await serverHasResponseForRequest([submitted], marker), false);
  assert.equal(
    await serverHasResponseForRequest(
      [submitted, assistantMessage("completed response")],
      marker,
    ),
    true,
  );
});

test("distinguishes repeated identical prompts by occurrence", async () => {
  const repeated = userMessage("run it again");
  const marker = {
    fingerprint: await userMessageFingerprint(repeated),
    occurrence: 2,
  };

  assert.equal(
    await serverHasResponseForRequest(
      [repeated, assistantMessage("first response"), repeated],
      marker,
    ),
    false,
  );
  assert.equal(
    await serverHasResponseForRequest(
      [
        repeated,
        assistantMessage("first response"),
        repeated,
        assistantMessage("second response"),
      ],
      marker,
    ),
    true,
  );
});

test("matches persisted attachments when the adapter drops the filename", async () => {
  const submitted = {
    role: "user",
    parts: [
      {
        type: "file",
        mediaType: "application/pdf",
        filename: "private-lease.pdf",
        url: "data:application/pdf;base64,submitted",
      },
    ],
  };
  const persisted = {
    role: "user",
    parts: [
      {
        type: "file",
        mediaType: "application/pdf",
        filename: null,
        url: "data:application/pdf;base64,persisted",
      },
    ],
  };
  const marker = {
    fingerprint: await userMessageFingerprint(submitted),
    occurrence: 1,
  };

  assert.equal(
    await serverHasResponseForRequest(
      [persisted, assistantMessage("attachment reviewed")],
      marker,
    ),
    true,
  );
});
