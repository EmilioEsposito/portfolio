import { redirect } from "react-router";

/**
 * A permanent redirect that browsers check again after a day.
 *
 * Chrome keeps a permanent redirect (301 or 308) that has no expiry for good
 * and stops asking the server, so the address can never get a page of its own
 * again in a browser that followed it. serniacapital.com/ was stuck that way
 * in 2026; see ~/sites/team/lib/forget-old-home-redirect. Every permanent
 * redirect in the app goes through here; test/lib/redirects.test.ts checks.
 * A redirect that may change soon should be a plain `redirect()` (302).
 */
export function movedTo(to: string): Response {
  return redirect(to, { status: 301, headers: { "Cache-Control": "max-age=86400" } });
}
