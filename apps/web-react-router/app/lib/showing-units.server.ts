import { backendUrl } from "~/lib/backend-url.server";
import { PUBLIC_API } from "~/lib/showing-booking-page";
import type { UnitsOnOffer, UnitsState } from "~/lib/showing-units";

/** How long the Sernia home page waits for the call line's route before rendering without it. */
export const CALL_LINE_TIMEOUT_MS = 3_000;

export type CallLineState = { bookingOpen: boolean; turnstileSiteKey: string | null };

const NO_CALL_LINE: CallLineState = { bookingOpen: false, turnstileSiteKey: null };

/**
 * Whether the call line can work, and its Turnstile site key, from
 * `GET /call-line`, which reads no listings and no calendars. Off (404), slow
 * or failing all answer "no call line", so the home page never waits on it.
 */
export async function readCallLine(timeoutMs: number = CALL_LINE_TIMEOUT_MS): Promise<CallLineState> {
  try {
    const response = await fetch(`${backendUrl()}${PUBLIC_API}/call-line`, {
      headers: { accept: "application/json" },
      signal: AbortSignal.timeout(timeoutMs),
    });
    if (!response.ok) return NO_CALL_LINE;
    const body = (await response.json()) as { turnstile_site_key?: unknown };
    const key = typeof body?.turnstile_site_key === "string" ? body.turnstile_site_key : null;
    return { bookingOpen: true, turnstileSiteKey: key };
  } catch {
    return NO_CALL_LINE;
  }
}

/** The units on offer, read on the server for the list at /showings and the Sernia home page. */
export async function readUnitsOnOffer(): Promise<UnitsState> {
  let response: Response;
  try {
    response = await fetch(`${backendUrl()}${PUBLIC_API}/units`, {
      headers: { accept: "application/json" },
      signal: AbortSignal.timeout(15_000),
    });
  } catch {
    return { status: "unavailable" };
  }
  // The public routes answer 404 while public booking is switched off.
  if (response.status === 404) return { status: "closed" };
  if (!response.ok) return { status: "unavailable" };
  const body = (await response.json().catch(() => null)) as UnitsOnOffer | null;
  if (!body || !Array.isArray(body.units)) return { status: "unavailable" };
  return { status: "open", units: body.units, turnstileSiteKey: body.turnstile_site_key };
}
