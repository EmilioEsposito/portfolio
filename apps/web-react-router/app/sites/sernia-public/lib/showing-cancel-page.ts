/**
 * The cancel page's logic, kept apart from React so it can be tested: which
 * state a booking is in, what each state says, and what the page does with
 * the cancel route's answer.
 *
 * The page's URL holds the cancel token, as the link in the text does. The
 * token goes to the API in a request body, never in an API path.
 */
import { type BookingView, NETWORK_MESSAGE, SERVER_MESSAGE, readRefusal } from "./showing-booking-page";

/** What the page shows. An unknown link is the route's 404, not a state. */
export type CancelState =
  /** Details and the Cancel button. */
  | "booked"
  /** The person pressed Cancel on this page. */
  | "cancelled_now"
  /** Cancelled before the page was opened, by the person or a withdrawn number. */
  | "already_cancelled"
  /** The team cancelled it, or the unit went off the market. */
  | "cancelled_by_team"
  /** Still booked, but the start has passed. */
  | "started";

export function cancelState(view: Pick<BookingView, "status" | "started" | "cancelled_by_team">, justCancelled = false): CancelState {
  if (view.status === "booked") return view.started ? "started" : "booked";
  if (view.cancelled_by_team) return "cancelled_by_team";
  return justCancelled ? "cancelled_now" : "already_cancelled";
}

/** The page a cancel link opens. */
export function cancelPath(token: string): string {
  return `/showings/cancel/${encodeURIComponent(token)}`;
}

/**
 * The cancel token in a calendar file's name, `<token>.ics`, or null for any other
 * name. The confirmation email links /showings/calendar/<token>.ics.
 */
export function calendarFileToken(file: string | undefined): string | null {
  const match = /^([A-Za-z0-9_-]{1,100})\.ics$/.exec(file ?? "");
  return match ? match[1] : null;
}

/** The unit's booking page, where another time is a new booking. */
export function unitPath(unit: string): string {
  return `/showings/${encodeURIComponent(unit)}`;
}

/** The units still on offer. */
export const UNITS_PATH = "/showings";

export type CancelCopy = {
  title: string;
  heading: string;
  /** The line under the details, if any. */
  line: string | null;
  /** Where to go next, if anywhere. */
  next: { label: string; href: string } | null;
};

export function cancelCopy(state: CancelState, unit: string): CancelCopy {
  switch (state) {
    case "booked":
      return { title: "Cancel your showing", heading: "Cancel your showing?", line: null, next: null };
    case "cancelled_now":
      return {
        title: "Showing cancelled",
        heading: "Your showing is cancelled",
        line: null,
        next: { label: "Book another time", href: unitPath(unit) },
      };
    case "already_cancelled":
      return {
        title: "Showing cancelled",
        heading: "This showing is already cancelled",
        line: null,
        next: { label: "Book another time", href: unitPath(unit) },
      };
    case "cancelled_by_team":
      return {
        title: "Showing cancelled",
        heading: "We cancelled this showing",
        line: null,
        next: { label: "See units open for showings", href: UNITS_PATH },
      };
    case "started":
      return {
        title: "Showing started",
        heading: "This showing has started",
        line: "It can no longer be cancelled here.",
        next: null,
      };
  }
}

/** What the page does with the cancel route's answer. */
export type CancelOutcome =
  | { kind: "view"; view: BookingView }
  /** The start passed while the page was open. */
  | { kind: "started" }
  /** The booking is gone: the link was wrong, or booking is switched off. */
  | { kind: "unknown"; message: string }
  /** Nothing changed; the line to show beside the button. */
  | { kind: "error"; message: string };

export const UNKNOWN_MESSAGE = "We couldn't find this booking. The link may be incomplete.";

export function readCancelAnswer(status: number | null, body: unknown): CancelOutcome {
  if (status === null) return { kind: "error", message: NETWORK_MESSAGE };
  if (status >= 200 && status < 300 && isView(body)) return { kind: "view", view: body };
  const refusal = readRefusal(status, body);
  if (refusal.code === "started") return { kind: "started" };
  if (status === 404) return { kind: "unknown", message: UNKNOWN_MESSAGE };
  return { kind: "error", message: status >= 500 || refusal.code === "unknown" ? SERVER_MESSAGE : refusal.message };
}

function isView(body: unknown): body is BookingView {
  return typeof body === "object" && body !== null && "status" in body && "starts_at" in body;
}
