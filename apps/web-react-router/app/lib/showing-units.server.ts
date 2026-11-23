import { isbot } from "isbot";

import { backendUrl } from "~/lib/backend-url.server";
import { readAhead, takeReadAhead } from "~/lib/read-ahead.server";
import { PUBLIC_API, type UnitPage } from "~/lib/showing-booking-page";
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

/** The units on offer, read on the server for the list at /showings. */
async function readUnitsOnOffer(): Promise<UnitsState> {
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

/** One unit as its booking page shows it: open, not open to this person (404), or not readable. */
export type UnitState = { status: "open"; page: UnitPage } | { status: "not-open" } | { status: "unavailable" };

async function readUnit(unit: string): Promise<UnitState> {
  try {
    const response = await fetch(`${backendUrl()}${PUBLIC_API}/units/${encodeURIComponent(unit)}`, {
      headers: { accept: "application/json" },
      signal: AbortSignal.timeout(15_000),
    });
    if (response.status === 404) return { status: "not-open" };
    if (!response.ok) return { status: "unavailable" };
    return { status: "open", page: (await response.json()) as UnitPage };
  } catch {
    return { status: "unavailable" };
  }
}

// The API reads the team's calendars for both, which takes a second or two. So
// each is read ahead from the page that links to it (read-ahead.server.ts),
// and opening it seldom waits. A bot opens pages in no such order, so its page
// views start no reads.

const LIST = "units";
const unitKey = (unit: string) => `unit ${unit}`;
/** How many of a list's booking pages are read ahead. Past this, the visitor waits for the one they open. */
const PAGES_READ_AHEAD = 6;

/** From the home page, which links to the list. */
export function readListAhead(request: Request): void {
  if (!isbot(request.headers.get("user-agent"))) readAhead(LIST, readUnitsOnOffer);
}

/** The list for /showings. Once it is known, the booking pages it links to are read ahead. */
export async function listForPage(request: Request): Promise<UnitsState> {
  const ahead = await takeReadAhead<UnitsState>(LIST, request);
  const list = ahead?.status === "open" ? ahead : await readUnitsOnOffer();
  if (list.status === "open" && !isbot(request.headers.get("user-agent"))) {
    for (const { unit } of list.units.slice(0, PAGES_READ_AHEAD)) readAhead(unitKey(unit), () => readUnit(unit));
  }
  return list;
}

/** One unit for its booking page. An answer read ahead counts only while the unit was open. */
export async function unitForPage(unit: string, request: Request): Promise<UnitState> {
  const ahead = await takeReadAhead<UnitState>(unitKey(unit), request);
  return ahead?.status === "open" ? ahead : readUnit(unit);
}
