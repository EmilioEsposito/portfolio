/**
 * API answers read before the page that needs them is opened. A page starts
 * the read for the page its visitor is likely to open next, without waiting
 * for it, and that page's loader takes the answer, finished or not.
 *
 * Only a navigation inside the site takes one. A page load always asks the
 * API, so a reload shows what is true now. The answers are the same for
 * everyone, so they are kept per process and not per visitor: never put
 * anything here that depends on who asks.
 */
const MAX_AGE_MS = 60_000;

const reads = new Map<string, { startedAt: number; answer: Promise<unknown> }>();

/** Starts a read for a page not asked for yet. `read` must not reject. */
export function readAhead<T>(key: string, read: () => Promise<T>): void {
  const now = Date.now();
  for (const [other, { startedAt }] of reads) {
    if (now - startedAt > MAX_AGE_MS) reads.delete(other);
  }
  reads.set(key, { startedAt: now, answer: read() });
}

/** The answer of a read started in the last minute, for a navigation inside the site; else null. */
export function takeReadAhead<T>(key: string, request: Request): Promise<T> | null {
  if (!isDataRequest(request)) return null;
  const held = reads.get(key);
  if (!held || Date.now() - held.startedAt > MAX_AGE_MS) return null;
  return held.answer as Promise<T>;
}

/** Drops every answer. For tests. */
export function forgetReadAhead(): void {
  reads.clear();
}

/** Whether the request is React Router's own, for a navigation inside the site. */
function isDataRequest(request: Request): boolean {
  return new URL(request.url).pathname.endsWith(".data");
}
