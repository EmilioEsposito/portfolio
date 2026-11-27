/**
 * Until the Sernia home page launched, serniacapital.com/ answered with a
 * permanent redirect to the Buildium resident portal. Chrome keeps a permanent
 * redirect with no expiry, so a browser that followed the team's "Public site"
 * link back then kept opening the portal from that link.
 *
 * Asking for "/" with the browser's cache skipped makes the browser replace
 * that stored redirect with today's home page. It runs once per browser, from
 * the team layout, on a host whose public site is at "/".
 *
 * Chrome files a page reached by typing its address separately from one
 * reached by a link on the same site, and nothing a page runs reaches the
 * typed one: only clearing the browser's cache removes that. (Measured in
 * Chrome on 2026-11-27; a `Clear-Site-Data` header would reach it but held a
 * page load for over a minute, so it is not used.)
 */

export const OLD_HOME_REDIRECT_FORGOTTEN_KEY = "sernia:old-home-redirect-forgotten";

type Fetcher = (input: string, init: RequestInit) => Promise<Pick<Response, "ok">>;
type Store = Pick<Storage, "getItem" | "setItem">;

/** Refreshes the browser's stored copy of "/"; returns whether it asked the server. */
export async function forgetOldHomeRedirect(
  fetcher: Fetcher | null = typeof fetch === "undefined" ? null : fetch,
  store: Store | null = typeof localStorage === "undefined" ? null : localStorage,
): Promise<boolean> {
  if (!fetcher || !store) return false;
  if (store.getItem(OLD_HOME_REDIRECT_FORGOTTEN_KEY)) return false;
  const response = await fetcher("/", { cache: "reload", credentials: "same-origin" });
  // A failed answer leaves the key unset, so the next page load tries again.
  if (response.ok) store.setItem(OLD_HOME_REDIRECT_FORGOTTEN_KEY, new Date().toISOString());
  return true;
}

/** Runs forgetOldHomeRedirect() in the background, ignoring failures (storage blocked, offline). */
export function forgetOldHomeRedirectOnce(): void {
  void forgetOldHomeRedirect().catch(() => false);
}
