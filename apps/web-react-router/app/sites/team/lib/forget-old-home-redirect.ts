/**
 * Until the Sernia home page launched, serniacapital.com/ answered with a
 * permanent redirect to the Buildium resident portal. Chrome keeps a permanent
 * redirect with no expiry, so a browser that opened serniacapital.com back
 * then still opens the portal.
 *
 * The team layout asks for CLEAR_SITE_CACHE_PATH once per browser, in the
 * background. Its answer carries `Clear-Site-Data: "cache"`, which makes the
 * browser drop everything it cached for this site, that redirect included.
 * Cookies, and so sign-ins, stay.
 *
 * Measured in Chrome on 2026-11-27:
 * - Clearing takes a while: 45s to 86s.
 * - From a background request the page stays usable, and other requests
 *   answer at their usual speed while it runs. The same header on a page's
 *   own response held that page blank for 73s, so it is never sent that way.
 * - A plain request for "/" that skips the cache is not enough: Chrome files
 *   a page reached by typing its address apart from one reached by a link,
 *   and such a request only replaces the link's copy.
 */

export const SITE_CACHE_CLEARED_KEY = "sernia:site-cache-cleared";

/** The team route whose answer clears this site's cache in the browser. */
export const CLEAR_SITE_CACHE_PATH = "/team/clear-cache";

/** The answer that makes the browser drop everything it cached for this site. */
export function clearSiteCacheResponse(): Response {
  return new Response(null, {
    status: 204,
    headers: { "Clear-Site-Data": '"cache"', "Cache-Control": "no-store" },
  });
}

type Fetcher = (input: string, init: RequestInit) => Promise<Pick<Response, "ok">>;
type Store = Pick<Storage, "getItem" | "setItem">;

/** Clears this site's cache in the browser unless it has been done; returns whether it asked the server. */
export async function forgetOldHomeRedirect(
  fetcher: Fetcher | null = typeof fetch === "undefined" ? null : fetch,
  store: Store | null = typeof localStorage === "undefined" ? null : localStorage,
): Promise<boolean> {
  if (!fetcher || !store) return false;
  if (store.getItem(SITE_CACHE_CLEARED_KEY)) return false;
  const response = await fetcher(CLEAR_SITE_CACHE_PATH, { cache: "no-store", credentials: "same-origin" });
  // A failed answer, or a tab closed before the answer came, leaves the key
  // unset, so a later page load tries again.
  if (response.ok) store.setItem(SITE_CACHE_CLEARED_KEY, new Date().toISOString());
  return true;
}

let started = false;

/** Runs forgetOldHomeRedirect() once per page load in the background, ignoring failures (storage blocked, offline). */
export function forgetOldHomeRedirectOnce(): void {
  if (started) return;
  started = true;
  void forgetOldHomeRedirect().catch(() => false);
}
