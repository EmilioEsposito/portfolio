import { wwwTwinHost } from "~/sites/site";

/**
 * Until the Sernia home page launched, serniacapital.com/ and
 * www.serniacapital.com/ answered with a permanent redirect to the Buildium
 * resident portal. Chrome keeps a permanent redirect with no expiry, so a
 * browser that opened either address back then still opens the portal.
 *
 * The team layout asks for CLEAR_SITE_CACHE_PATH in the background, on its own
 * host and on the host's `www.` twin. Each answer carries
 * `Clear-Site-Data: "cache"`, which makes the browser drop everything it
 * cached for the host that answered, that redirect included. Cookies, and so
 * sign-ins, stay.
 *
 * Measured in Chrome on 2026-11-27:
 * - Clearing takes a while: 37s to 86s, and about 4s in a nearly empty
 *   browser. The Clear Site Data spec notes why: a cache filed by address
 *   has to be gone through whole to find one host's entries.
 * - From a background request the page stays usable, and other requests
 *   answer at their usual speed while it runs. The same header on a page's
 *   own response held that page blank for 73s, so it is never sent that way.
 * - Once the request is sent, the clearing finishes even if the tab is closed
 *   two seconds later. The page then never hears the answer, which is why a
 *   browser stops after MAX_TRIES and does not start over on every visit.
 * - The browser clears only the host that answered. For the twin, a request
 *   to another host, it runs the clearing only when the request carries
 *   credentials.
 * - A plain request for "/" that skips the cache is not enough: Chrome files
 *   a page reached by typing its address apart from one reached by a link,
 *   and such a request only replaces the link's copy.
 */

/** Holds the number of tries so far, then the time the clearing was seen to finish. */
export const SITE_CACHE_CLEARED_KEY = "sernia:site-cache-cleared";

/** The team path whose answer clears the answering host's cache in the browser. */
export const CLEAR_SITE_CACHE_PATH = "/team/clear-cache";

const MAX_TRIES = 3;

/** The answer that makes the browser drop everything it cached for this host. */
export function clearSiteCacheResponse(): Response {
  return new Response(null, {
    status: 204,
    headers: { "Clear-Site-Data": '"cache"', "Cache-Control": "no-store" },
  });
}

/**
 * What a browser on this team page asks for to clear its cache: this host's
 * path first, then the `www.` twin's address if there is one. Empty where the
 * public site is not at "/", since such a host never sent the redirect.
 */
export function clearSiteCacheUrls(url: URL, publicSiteAtRoot: boolean): string[] {
  if (!publicSiteAtRoot) return [];
  const twin = wwwTwinHost(url);
  // No scheme, so the request uses the page's own.
  return twin ? [CLEAR_SITE_CACHE_PATH, `//${twin}${CLEAR_SITE_CACHE_PATH}`] : [CLEAR_SITE_CACHE_PATH];
}

type Fetcher = (input: string, init: RequestInit) => Promise<Pick<Response, "ok">>;
type Store = Pick<Storage, "getItem" | "setItem">;

function triesSoFar(state: string | null): number | "finished" {
  if (state === null) return 0;
  return /^\d+$/.test(state) ? Number(state) : "finished";
}

/**
 * Clears the cache of this host and its twin, unless this browser has seen
 * that finish or has tried MAX_TRIES times. Returns whether it asked.
 */
export async function forgetOldHomeRedirect(
  urls: readonly string[],
  fetcher: Fetcher | null = typeof fetch === "undefined" ? null : fetch,
  store: Store | null = typeof localStorage === "undefined" ? null : localStorage,
): Promise<boolean> {
  const [own, ...twins] = urls;
  if (!fetcher || !store || !own) return false;
  const tries = triesSoFar(store.getItem(SITE_CACHE_CLEARED_KEY));
  if (tries === "finished" || tries >= MAX_TRIES) return false;
  store.setItem(SITE_CACHE_CLEARED_KEY, String(tries + 1));
  const cleared = await Promise.all([
    fetcher(own, { cache: "no-store", credentials: "same-origin" }).then((response) => response.ok),
    // Another host's answer cannot be read, only waited for.
    ...twins.map((url) => fetcher(url, { mode: "no-cors", cache: "no-store", credentials: "include" }).then(() => true)),
  ]);
  if (cleared.every(Boolean)) store.setItem(SITE_CACHE_CLEARED_KEY, new Date().toISOString());
  return true;
}

let started = false;

/** Runs forgetOldHomeRedirect() once per page load in the background, ignoring failures (storage blocked, offline). */
export function forgetOldHomeRedirectOnce(urls: readonly string[]): void {
  if (started || urls.length === 0) return;
  started = true;
  void forgetOldHomeRedirect(urls).catch(() => false);
}
