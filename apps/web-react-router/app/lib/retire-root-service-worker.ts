/**
 * Before the team's pages moved to /team, the staff app registered its service
 * worker for the whole site (scope "/") and subscribed it to push
 * notifications. This drops that worker's push subscription and unregisters
 * it, so an old install stops receiving notifications that open pages that no
 * longer exist. The team's own worker (scope "/team/") is left alone.
 *
 * Portfolio and team pages run it once per page load. It does nothing where
 * no such worker is registered, which after the first run is everywhere.
 */

type Registrations = Pick<ServiceWorkerContainer, "getRegistrations">;

/** Unregisters the worker whose scope is the whole origin; returns how many it removed. */
export async function retireRootServiceWorker(
  container: Registrations | undefined = typeof navigator === "undefined" ? undefined : navigator.serviceWorker,
  origin: string = typeof location === "undefined" ? "" : location.origin,
): Promise<number> {
  if (!container || !origin) return 0;
  let retired = 0;
  for (const registration of await container.getRegistrations()) {
    if (registration.scope !== `${origin}/`) continue;
    // The browser stops delivering to this subscription at once; the API
    // drops it the next time a send to it fails.
    const subscription = await registration.pushManager?.getSubscription().catch(() => null);
    await subscription?.unsubscribe().catch(() => false);
    if (await registration.unregister().catch(() => false)) retired += 1;
  }
  return retired;
}

let started = false;

/** Runs retireRootServiceWorker() once per page load, ignoring failures. */
export function retireRootServiceWorkerOnce(): void {
  if (started) return;
  started = true;
  void retireRootServiceWorker().catch(() => 0);
}
