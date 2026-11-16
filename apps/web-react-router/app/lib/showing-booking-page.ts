/**
 * The public booking page's logic, kept apart from React so it can be tested:
 * the API's shapes, times grouped and worded in the unit's time zone, and what
 * the page does with each refusal.
 *
 * Every time is shown in the time zone the API names for the unit, never the
 * browser's. Strings are built from Intl's parts with plain spaces, so the
 * server's render and the browser's agree whatever ICU version each has.
 */

/** Where the page calls the public booking API. The browser calls it on the page's own origin. */
export const PUBLIC_API = "/api/showing-bookings/public";

export type Confirmation = { key: string; wording: string; version: string };

export type Offer = { start: string; join: boolean; preferred: boolean; places_left: number };

/** `GET /units/{unit}`. */
export type UnitPage = {
  unit: string;
  address: string;
  rent: number | null;
  showing_minutes: number;
  timezone: string;
  confirmations: Confirmation[];
  confirmations_version: string;
  consent_small_print: string;
  privacy_path: string;
  sms_terms_path: string;
  turnstile_site_key: string | null;
  offers: Offer[];
};

/** A booking as the confirmation screen shows it. */
export type BookingView = {
  status: string;
  unit: string;
  address: string;
  starts_at: string;
  ends_at: string;
  timezone: string;
  mobile: string | null;
  corrections_left: number;
  /** The start has passed: it can no longer be cancelled or changed online. */
  started: boolean;
  /** Cancelled by the team, or because the unit went off the market. */
  cancelled_by_team: boolean;
};

/** `POST /units/{unit}/bookings`, 201. */
export type BookingResult = {
  cancel_token: string;
  kind: string;
  confirmation: string | null;
  booking: BookingView;
};

/** `POST /team-phone`. */
export type TeamPhone = { phone: string; display: string; extension: string; tel_href: string };

/** A refusal's `detail`. `fields` maps a form field to the line shown beside it. */
export type Refusal = { status: number; code: string; message: string; fields: Record<string, string> };

// ---------------------------------------------------------------------------
// Times
// ---------------------------------------------------------------------------

type Parts = Partial<Record<Intl.DateTimeFormatPartTypes, string>>;

const formatters = new Map<string, Intl.DateTimeFormat>();

function parts(iso: string, timeZone: string): Parts {
  let format = formatters.get(timeZone);
  if (!format) {
    format = new Intl.DateTimeFormat("en-US", {
      timeZone,
      weekday: "short",
      month: "short",
      day: "numeric",
      year: "numeric",
      hour: "numeric",
      minute: "2-digit",
      hour12: true,
    });
    formatters.set(timeZone, format);
  }
  const found: Parts = {};
  for (const part of format.formatToParts(new Date(iso))) found[part.type] = part.value;
  return found;
}

/** "Wed, Apr 14" in the time zone. */
export function formatDay(iso: string, timeZone: string): string {
  const p = parts(iso, timeZone);
  return `${p.weekday}, ${p.month} ${p.day}`;
}

/** "4:30 PM" in the time zone. */
export function formatTime(iso: string, timeZone: string): string {
  const p = parts(iso, timeZone);
  return `${p.hour}:${p.minute} ${(p.dayPeriod ?? "").toUpperCase()}`;
}

/** "Wed, Apr 14 at 4:30 PM". */
export function formatWhen(iso: string, timeZone: string): string {
  return `${formatDay(iso, timeZone)} at ${formatTime(iso, timeZone)}`;
}

/** "Wed, Apr 14, 4:00 to 4:30 PM", or both times with their AM/PM when they differ. */
export function formatSpan(startIso: string, endIso: string, timeZone: string): string {
  const start = formatTime(startIso, timeZone);
  const end = formatTime(endIso, timeZone);
  const startPeriod = start.slice(-2);
  const shownStart = startPeriod === end.slice(-2) ? start.slice(0, -3) : start;
  return `${formatDay(startIso, timeZone)}, ${shownStart} to ${end}`;
}

/** The calendar day in the time zone, "2027-04-14", for grouping. */
export function dayKey(iso: string, timeZone: string): string {
  const format = new Intl.DateTimeFormat("en-US", { timeZone, year: "numeric", month: "2-digit", day: "2-digit" });
  const p: Parts = {};
  for (const part of format.formatToParts(new Date(iso))) p[part.type] = part.value;
  return `${p.year}-${p.month}-${p.day}`;
}

export type TimeChoice = {
  start: string;
  label: string;
  /** A showing already booked, which this person would join. */
  join: boolean;
  preferred: boolean;
  /** What the time's button says after the time, if anything. */
  note: string | null;
};

export type DayGroup = { key: string; label: string; times: TimeChoice[] };

/**
 * A time's note: "Suggested" for the times the API ranks first, a showing
 * already booked (a join) or a preferred time. A join is not called out as
 * booked: that can read as taken, and any showing can become a group.
 */
export function timeNote(offer: Pick<Offer, "join" | "preferred">): string | null {
  return offer.join || offer.preferred ? "Suggested" : null;
}

/** The day the picker opens on: the day of the API's first offer, its best. */
export function bestDayKey(offers: Offer[], timeZone: string): string | null {
  return offers.length > 0 ? dayKey(offers[0].start, timeZone) : null;
}

/**
 * The offers by day in the unit's time zone, days in order and each day's
 * times in order. The API orders offers by kind (joins, then preferred, then
 * the rest); a person picks by day and time, so the page orders by time,
 * marks the best times "Suggested" and opens on the best one's day
 * (`bestDayKey`).
 */
export function groupOffers(offers: Offer[], timeZone: string): DayGroup[] {
  const sorted = [...offers].sort((a, b) => Date.parse(a.start) - Date.parse(b.start));
  const days: DayGroup[] = [];
  for (const offer of sorted) {
    const key = dayKey(offer.start, timeZone);
    let day = days.at(-1);
    if (!day || day.key !== key) {
      day = { key, label: formatDay(offer.start, timeZone), times: [] };
      days.push(day);
    }
    day.times.push({
      start: offer.start,
      label: formatTime(offer.start, timeZone),
      join: offer.join,
      preferred: offer.preferred,
      note: timeNote(offer),
    });
  }
  return days;
}

/** The offer for a start time, compared as instants so "…-04:00" and "…Z" match. */
export function findOffer(offers: Offer[], start: string | null): Offer | undefined {
  if (!start) return undefined;
  const wanted = Date.parse(start);
  return offers.find((offer) => Date.parse(offer.start) === wanted);
}

/** "$1,450 a month". */
export function monthlyRent(rent: number): string {
  return `$${rent.toLocaleString("en-US")} a month`;
}

/** "320 Example St, unit 320-03 · $1,450 a month · 30 minutes". */
export function unitSummary(unit: Pick<UnitPage, "unit" | "address" | "rent" | "showing_minutes">): string {
  const items = [bookingPlace(unit)];
  if (unit.rent !== null) items.push(monthlyRent(unit.rent));
  items.push(`${unit.showing_minutes} minutes`);
  return items.join(" · ");
}

/** "320 Example St, unit 320-03", or "Unit 320-03" with no address. */
export function bookingPlace(view: Pick<BookingView, "address" | "unit">): string {
  return view.address ? `${view.address}, unit ${view.unit}` : `Unit ${view.unit}`;
}

// ---------------------------------------------------------------------------
// The form
// ---------------------------------------------------------------------------

export type FormValues = {
  start: string | null;
  name: string;
  mobile: string;
  email: string;
  ticks: Record<string, boolean>;
};

export const EMPTY_FORM: FormValues = { start: null, name: "", mobile: "", email: "", ticks: {} };

/** A field's key, as the API names it: "start", "name", "confirmations.pets"… */
export const tickField = (key: string) => `confirmations.${key}`;

/** The order fields appear on the page, so focus goes to the first one marked. */
export function fieldOrder(confirmations: Pick<Confirmation, "key">[]): string[] {
  return ["start", "name", "mobile", "email", ...confirmations.map((c) => tickField(c.key)), "confirmations", "turnstile"];
}

export function firstMarked(fields: Record<string, string>, order: string[]): string | undefined {
  return order.find((name) => fields[name]) ?? Object.keys(fields)[0];
}

/** The lines the API would answer for what is missing, checked before the bot check runs. */
export function missingFields(values: FormValues, confirmations: Pick<Confirmation, "key">[]): Record<string, string> {
  const fields: Record<string, string> = {};
  if (!values.start) fields.start = "Pick a time.";
  if (!values.name.trim()) fields.name = "Enter your name.";
  if (!values.mobile.trim()) fields.mobile = "Enter a ten-digit US mobile number.";
  if (!values.email.trim()) fields.email = "Enter one email address.";
  for (const item of confirmations) {
    if (!values.ticks[item.key]) fields[tickField(item.key)] = "Tick this box to book online.";
  }
  return fields;
}

/** "One box still needs a tick. It is marked above." or the count, or null with none missing. */
export function ticksMessage(fields: Record<string, string>): string | null {
  const ticks = Object.keys(fields).filter((name) => name.startsWith("confirmations.")).length;
  if (ticks === 0) return null;
  return ticks === 1
    ? "One box still needs a tick. It is marked above."
    : `${ticks} boxes still need a tick. They are marked above.`;
}

/** The booking request's body. */
export function bookingBody(
  values: FormValues,
  unit: Pick<UnitPage, "confirmations" | "confirmations_version">,
  turnstileToken: string,
  leadToken: string | null,
) {
  const confirmations: Record<string, boolean> = {};
  for (const item of unit.confirmations) confirmations[item.key] = Boolean(values.ticks[item.key]);
  return {
    start: values.start ?? "",
    name: values.name,
    mobile: values.mobile,
    email: values.email,
    confirmations,
    confirmations_version: unit.confirmations_version,
    turnstile_token: turnstileToken,
    ...(leadToken ? { lead_token: leadToken } : {}),
  };
}

// ---------------------------------------------------------------------------
// Refusals
// ---------------------------------------------------------------------------

export const NETWORK_MESSAGE = "We couldn't reach Sernia Capital. Check your connection and try again.";
export const SERVER_MESSAGE = "Something went wrong on our side. Try again, or call us instead.";
export const BOT_CHECK_MESSAGE = "We couldn't run the check that you're not a bot. Try again, or call us instead.";
export const BOT_CHECK_RETRY = "We couldn't run the check that you're not a bot. Try again.";

/** An API answer's refusal, in one shape whatever the body held. */
export function readRefusal(status: number, body: unknown): Refusal {
  const detail =
    typeof body === "object" && body !== null && "detail" in body ? (body as { detail: unknown }).detail : undefined;
  if (typeof detail === "object" && detail !== null && "code" in detail) {
    const { code, message, fields } = detail as { code?: unknown; message?: unknown; fields?: unknown };
    const lines: Record<string, string> = {};
    if (typeof fields === "object" && fields !== null) {
      for (const [name, line] of Object.entries(fields)) if (typeof line === "string") lines[name] = line;
    }
    return {
      status,
      code: typeof code === "string" ? code : "unknown",
      message: typeof message === "string" && message ? message : SERVER_MESSAGE,
      fields: lines,
    };
  }
  // A 404 with no detail of ours is the switch (booking is off), as for a path that does not exist.
  if (status === 404) return { status, code: "unknown_unit", message: UNIT_GONE_MESSAGE, fields: {} };
  return { status, code: "unknown", message: SERVER_MESSAGE, fields: {} };
}

export const UNIT_GONE_MESSAGE = "This unit is no longer open for showings online. Call us instead.";

/** What the page does after a refusal of a booking, besides showing the lines beside their fields. */
export type RefusalAction =
  /** Load the offers again, clear the time and ask for another. */
  | "pick_again"
  /** Load the ticks' wording again and clear the ticks. */
  | "tick_again"
  /** Point to the call line: booking online cannot go ahead now. */
  | "call"
  /** Fix the marked fields and press Book again. */
  | "fix";

const CALL_CODES = new Set(["limit_ip", "limit_site", "unavailable", "unknown_unit", "unknown"]);

export function refusalAction(refusal: Pick<Refusal, "code">): RefusalAction {
  if (refusal.code === "time_gone" || refusal.code === "full") return "pick_again";
  if (refusal.code === "confirmations_changed") return "tick_again";
  if (CALL_CODES.has(refusal.code)) return "call";
  return "fix";
}

/**
 * The line shown above the Book button after a refusal. Where the API's lines
 * beside the fields say it all ("invalid"), a short pointer to them; a missed
 * tick gets the mockup's wording.
 */
export function refusalMessage(refusal: Refusal): string {
  if (refusal.code === "invalid") {
    const ticks = ticksMessage(refusal.fields);
    const onlyTicks = Object.keys(refusal.fields).every((name) => name.startsWith("confirmations."));
    return ticks && onlyTicks ? ticks : "Check the marked fields above.";
  }
  if (refusal.code === "unknown_unit") return UNIT_GONE_MESSAGE;
  return refusal.message;
}
