import assert from "node:assert/strict";
import test, { type TestContext } from "node:test";

import { AHEAD_LIMIT_MS, TOKEN_MAX_AGE_MS, TokenHolder } from "~/lib/turnstile-tokens";

// The holder of a token got ahead of time, with the checks answered by hand.

type Check = { pass: (token: string) => void; fail: () => void; signal?: AbortSignal };

function setUp(t: TestContext) {
  t.mock.timers.enable({ apis: ["Date", "setTimeout"], now: 1_000_000 });
  /** Checks started ahead of time, and by a person, in order. */
  const ahead: Check[] = [];
  const own: Check[] = [];
  let idle: (() => void)[] = [];
  const page = { mayRunAhead: true };

  function start(list: Check[], signal?: AbortSignal) {
    return new Promise<string>((resolve, reject) => {
      list.push({ pass: resolve, fail: () => reject(new Error("check failed")), signal });
      signal?.addEventListener("abort", () => reject(new Error("stopped")));
    });
  }

  const holder = new TokenHolder({
    runAhead: (signal) => start(ahead, signal),
    mayRunAhead: () => page.mayRunAhead,
    whenIdle: (run) => idle.push(run),
  });
  return {
    holder,
    ahead,
    own,
    page,
    /** A person asks for a token. */
    ask: () => holder.token(() => start(own)),
    /** The page becomes idle: what was waiting for that runs. */
    becomeIdle() {
      const due = idle;
      idle = [];
      for (const run of due) run();
    },
    /** Lets promises settle. */
    settle: () => new Promise<void>((resolve) => setImmediate(resolve)),
    wait: (ms: number) => t.mock.timers.tick(ms),
  };
}

test("a fresh token is given at once, with no new check", async (t) => {
  const { holder, ahead, own, ask, settle, wait } = setUp(t);
  holder.prepare();
  ahead[0].pass("one");
  await settle();
  wait(TOKEN_MAX_AGE_MS - 1);

  assert.equal(await ask(), "one");
  assert.equal(ahead.length, 1);
  assert.equal(own.length, 0);
});

test("an expired token is not given: the person's own check runs", async (t) => {
  const { holder, ahead, own, ask, settle, wait } = setUp(t);
  holder.prepare();
  ahead[0].pass("one");
  await settle();
  wait(TOKEN_MAX_AGE_MS);

  const token = ask();
  await settle();
  assert.equal(own.length, 1);
  own[0].pass("two");
  assert.equal(await token, "two");
});

test("an expired token is replaced the next time one is prepared, and a fresh one is not", async (t) => {
  const { holder, ahead, ask, settle, wait } = setUp(t);
  holder.prepare();
  holder.prepare();
  assert.equal(ahead.length, 1, "one check at a time");
  ahead[0].pass("one");
  await settle();
  holder.prepare();
  assert.equal(ahead.length, 1, "a fresh token is held");

  wait(TOKEN_MAX_AGE_MS);
  holder.prepare();
  assert.equal(ahead.length, 2);
  ahead[1].pass("two");
  await settle();
  assert.equal(await ask(), "two");
});

test("a check under way is waited for, not repeated", async (t) => {
  const { holder, ahead, own, ask, settle } = setUp(t);
  holder.prepare();
  const token = ask();
  await settle();
  assert.equal(own.length, 0);
  ahead[0].pass("one");
  assert.equal(await token, "one");
  assert.equal(own.length, 0);
});

test("once a token is used, the next is got ready when the page is idle", async (t) => {
  const { holder, ahead, ask, becomeIdle, settle } = setUp(t);
  holder.prepare();
  ahead[0].pass("one");
  await settle();
  assert.equal(await ask(), "one");
  assert.equal(ahead.length, 1, "not before the page is idle");

  becomeIdle();
  assert.equal(ahead.length, 2);
  ahead[1].pass("two");
  await settle();
  assert.equal(await ask(), "two");
});

test("a token from a check that was waited for is not given again", async (t) => {
  const { holder, ahead, own, ask, settle } = setUp(t);
  holder.prepare();
  const first = ask();
  ahead[0].pass("one");
  assert.equal(await first, "one");
  await settle();

  const second = ask();
  await settle();
  assert.equal(own.length, 1);
  own[0].pass("two");
  assert.equal(await second, "two");
});

test("with nothing ready, the person's own check runs, and the next token is got ready", async (t) => {
  const { ahead, own, ask, becomeIdle, settle } = setUp(t);
  const token = ask();
  await settle();
  assert.equal(own.length, 1);
  own[0].pass("one");
  assert.equal(await token, "one");
  becomeIdle();
  assert.equal(ahead.length, 1);
});

test("a check that fails ahead of time is silent, and runs again only when the person acts", async (t) => {
  const { holder, ahead, own, ask, becomeIdle, settle } = setUp(t);
  holder.prepare();
  ahead[0].fail();
  await settle();
  holder.prepare();
  assert.equal(ahead.length, 1, "not repeated on its own");

  const token = ask();
  await settle();
  assert.equal(own.length, 1);
  own[0].pass("two");
  assert.equal(await token, "two");

  // The person's own check passed, so checks run ahead of time again.
  becomeIdle();
  assert.equal(ahead.length, 2);
});

test("a check under way that fails while the person waits is followed by their own", async (t) => {
  const { holder, ahead, own, ask, settle } = setUp(t);
  holder.prepare();
  const token = ask();
  ahead[0].fail();
  await settle();
  assert.equal(own.length, 1);
  own[0].pass("two");
  assert.equal(await token, "two");
});

test("a person's own check that fails says so, and asking again runs another", async (t) => {
  const { ahead, own, ask, becomeIdle, settle } = setUp(t);
  const first = ask();
  await settle();
  own[0].fail();
  await assert.rejects(first, /check failed/);
  becomeIdle();
  assert.equal(ahead.length, 0, "nothing runs ahead of time after a failure");

  const second = ask();
  await settle();
  assert.equal(own.length, 2);
  own[1].pass("two");
  assert.equal(await second, "two");
});

test("a check ahead of time that never answers is dropped, so a person never waits long on it", async (t) => {
  const { holder, ahead, own, ask, settle, wait } = setUp(t);
  holder.prepare();
  const token = ask();
  wait(AHEAD_LIMIT_MS - 1);
  await settle();
  assert.equal(own.length, 0);
  wait(1);
  await settle();
  assert.equal(ahead[0].signal?.aborted, true);
  assert.equal(own.length, 1);
  own[0].pass("two");
  assert.equal(await token, "two");
});

test("two users never get the same token, and neither holds the other up", async (t) => {
  const { holder, ahead, own, ask, becomeIdle, settle } = setUp(t);
  // One takes the held token; the other has a check of their own.
  holder.prepare();
  ahead[0].pass("one");
  await settle();
  const [first, second] = [ask(), ask()];
  assert.equal(await first, "one");
  await settle();
  assert.equal(own.length, 1);
  own[0].pass("two");
  assert.equal(await second, "two");

  // One waits for the check under way; the other has a check of their own.
  becomeIdle();
  assert.equal(ahead.length, 2);
  const [third, fourth] = [ask(), ask()];
  await settle();
  assert.equal(own.length, 2);
  own[1].pass("four");
  assert.equal(await fourth, "four");
  ahead[1].pass("three");
  assert.equal(await third, "three");
});

test("nothing runs ahead of time while the page may not, but a person is always served", async (t) => {
  const { holder, ahead, own, page, ask, becomeIdle, settle } = setUp(t);
  page.mayRunAhead = false;
  holder.prepare();
  const token = ask();
  await settle();
  own[0].pass("one");
  assert.equal(await token, "one");
  becomeIdle();
  assert.equal(ahead.length, 0);

  page.mayRunAhead = true;
  holder.prepare();
  assert.equal(ahead.length, 1);
});
