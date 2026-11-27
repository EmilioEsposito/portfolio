import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { movedTo } from "../../app/lib/moved.ts";

// A browser keeps a permanent redirect (301 or 308) that has no expiry for
// good and stops asking the server, so the address can never get a page of
// its own again there. serniacapital.com/ was stuck that way in 2026. So the
// app sends a permanent redirect only through movedTo(), which expires.

const APP = fileURLToPath(new URL("../../app/", import.meta.url));
const HELPER = "lib/moved.ts";

function sourceFiles(folder = ""): string[] {
  return readdirSync(`${APP}${folder}`, { withFileTypes: true }).flatMap((entry) => {
    const path = `${folder}${entry.name}`;
    if (entry.isDirectory()) return sourceFiles(`${path}/`);
    return /\.(ts|tsx)$/.test(entry.name) ? [path] : [];
  });
}

/** A redirect call that names a permanent status, as a number or as `status:`. */
const PERMANENT = /\bredirect(?:Document)?\s*\([^;]*?\b30[18]\b/;

test("a permanent redirect expires after a day", () => {
  const response = movedTo("/somewhere");
  assert.deepEqual(
    [response.status, response.headers.get("Location"), response.headers.get("Cache-Control")],
    [301, "/somewhere", "max-age=86400"],
  );
});

test("no other code sends a permanent redirect", () => {
  const files = sourceFiles();
  assert.ok(files.length > 100, `expected the app's source files, found ${files.length}`);
  const permanent = files.filter((file) => file !== HELPER && PERMANENT.test(readFileSync(`${APP}${file}`, "utf8")));
  assert.deepEqual(permanent, [], "use movedTo() from ~/lib/moved, or a temporary redirect");
});

test("the check sees a permanent redirect in either form", () => {
  assert.match('throw redirect(moved, 308);', PERMANENT);
  assert.match('return redirect("/x", { status: 301 });', PERMANENT);
  assert.match(readFileSync(`${APP}${HELPER}`, "utf8"), PERMANENT);
  assert.doesNotMatch('return redirect("/multi-agent-chat");', PERMANENT);
  assert.doesNotMatch('throw redirect(moved, { status: 307, headers });', PERMANENT);
});
