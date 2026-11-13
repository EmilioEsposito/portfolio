// Rental listings as the /api/rental-listings endpoints send and take it, and the
// small pieces of logic the edit screen needs: comparing edits, reading the
// server's reasons for refusing a save, and describing a change in history.
// Shapes mirror api/src/rental_listings/schema.py.

export const WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"] as const;
export type Weekday = (typeof WEEKDAYS)[number];

export const WEEKDAY_LABELS: Record<Weekday, string> = {
  mon: "Mon",
  tue: "Tue",
  wed: "Wed",
  thu: "Thu",
  fri: "Fri",
  sat: "Sat",
  sun: "Sun",
};

export interface PreferredWindow {
  days: Weekday[];
  start: string; // "HH:MM" or "HH:MM:SS"
  end: string;
}

export interface RentalListing {
  unit: string;
  address: string;
  asking_rent: number | null;
  next_lease_start_earliest: string | null; // "YYYY-MM-DD"
  next_lease_start_latest: string | null;
  virtual_tours_allowed: boolean;
  off_market_on: string | null;
  showing_booking_url: string;
  preferred_showing_times: PreferredWindow[];
  public_facts: string[];
  internal_notes: string[];
}

export interface ShowingRules {
  timezone: string;
  earliest_start: string;
  latest_start: string;
  blackout_dates: string[];
  showing_booking_url: string;
}

export interface RentalData {
  listings: RentalListing[];
  showing_rules: ShowingRules;
}

export interface Change {
  subject: string; // a unit's name, or SHOWING_RULES_SUBJECT
  changed_at: string;
  changed_by: string;
  source: string | null;
  snapshot: Record<string, unknown>;
}

export const SHOWING_RULES_SUBJECT = "showing_rules";

export function newListing(name: string): RentalListing {
  return {
    unit: name,
    address: "",
    asking_rent: null,
    next_lease_start_earliest: null,
    next_lease_start_latest: null,
    virtual_tours_allowed: false,
    off_market_on: null,
    showing_booking_url: "",
    preferred_showing_times: [],
    public_facts: [],
    internal_notes: [],
  };
}

/** "15:00:00" and "15:00" are the same time; the form's time inputs use the short form. */
export function shortTime(value: string): string {
  return value.slice(0, 5);
}

function cleanLines(lines: string[]): string[] {
  return lines.map((line) => line.trim()).filter(Boolean);
}

/**
 * The unit as it should be sent: text trimmed, blank lines dropped, days in
 * week order and times in one form. Two edits that differ only in those ways
 * are the same edit.
 */
export function cleanListing(unit: RentalListing): RentalListing {
  return {
    ...unit,
    unit: unit.unit.trim(),
    address: unit.address.trim(),
    showing_booking_url: unit.showing_booking_url.trim(),
    next_lease_start_earliest: unit.next_lease_start_earliest || null,
    next_lease_start_latest: unit.next_lease_start_latest || null,
    off_market_on: unit.off_market_on || null,
    preferred_showing_times: unit.preferred_showing_times.map((window) => ({
      days: WEEKDAYS.filter((day) => window.days.includes(day)),
      start: shortTime(window.start),
      end: shortTime(window.end),
    })),
    public_facts: cleanLines(unit.public_facts),
    internal_notes: cleanLines(unit.internal_notes),
  };
}

export function cleanRules(rules: ShowingRules): ShowingRules {
  return {
    ...rules,
    timezone: rules.timezone.trim(),
    showing_booking_url: rules.showing_booking_url.trim(),
    earliest_start: shortTime(rules.earliest_start),
    latest_start: shortTime(rules.latest_start),
    blackout_dates: [...new Set(rules.blackout_dates.filter(Boolean))].sort(),
  };
}

export function sameListing(a: RentalListing, b: RentalListing): boolean {
  return JSON.stringify(cleanListing(a)) === JSON.stringify(cleanListing(b));
}

export function sameRules(a: ShowingRules, b: ShowingRules): boolean {
  return JSON.stringify(cleanRules(a)) === JSON.stringify(cleanRules(b));
}

/** A unit is no longer on offer from its off-market date on. */
export function isOffMarket(unit: RentalListing, today: string): boolean {
  return unit.off_market_on !== null && unit.off_market_on <= today;
}

/** Today's date in the browser's time zone, as "YYYY-MM-DD". */
export function localToday(now: Date = new Date()): string {
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
}

/**
 * Why the server did not save, as lines to show. The rental unit endpoints
 * answer `{detail: {message, problems}}`; FastAPI's own type checks answer
 * `{detail: [{loc, msg}]}`; anything else falls back to the status.
 */
export function problemsFrom(status: number, body: unknown): { message: string; problems: string[] } {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    const { message, problems } = detail as { message?: unknown; problems?: unknown };
    return {
      message: typeof message === "string" ? message : "Not saved.",
      problems: Array.isArray(problems) ? problems.map(String) : [],
    };
  }
  if (Array.isArray(detail)) {
    return {
      message: "Not saved.",
      problems: detail.map((item) => {
        const { loc, msg } = item as { loc?: unknown[]; msg?: string };
        // loc is ["body", "unit", "asking_rent"]; the field name is what a person needs.
        const field = (loc ?? []).filter((part) => part !== "body").slice(1).join(".");
        return field ? `${field}: ${msg}` : String(msg);
      }),
    };
  }
  if (typeof detail === "string") return { message: detail, problems: [] };
  if (status === 401) return { message: "You are signed out. Sign in again.", problems: [] };
  return { message: `Request failed (${status}).`, problems: [] };
}

const UNIT_FIELD_LABELS: Record<string, string> = {
  unit: "Name",
  address: "Address",
  asking_rent: "Asking rent",
  next_lease_start_earliest: "Next lease: earliest start",
  next_lease_start_latest: "Next lease: latest start",
  virtual_tours_allowed: "Virtual tours allowed",
  off_market_on: "Off market from",
  showing_booking_url: "Showing booking link",
  preferred_showing_times: "Preferred showing times",
  public_facts: "Public facts",
  internal_notes: "Internal notes",
  timezone: "Time zone",
  earliest_start: "Earliest showing start",
  latest_start: "Latest showing start",
  blackout_dates: "Blackout dates",
};

function describeValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "none";
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (Array.isArray(value)) {
    if (value.length === 0) return "none";
    return value.map((item) => (typeof item === "object" ? describeWindow(item) : String(item))).join(", ");
  }
  return String(value);
}

export function describeWindow(window: PreferredWindow): string {
  const days = WEEKDAYS.filter((day) => window.days.includes(day)).map((day) => WEEKDAY_LABELS[day]);
  return `${days.join("/")} ${shortTime(window.start)}–${shortTime(window.end)}`;
}

const LINE_FIELDS = new Set(["public_facts", "internal_notes", "blackout_dates"]);

/**
 * What one saved change did, as short lines, by comparing its snapshot with
 * the one before it. With no earlier snapshot, the change created the record.
 */
export function describeChange(
  after: Record<string, unknown>,
  before: Record<string, unknown> | null,
): string[] {
  if (before === null) return ["Created"];
  const lines: string[] = [];
  for (const field of Object.keys({ ...before, ...after })) {
    const was = before[field];
    const now = after[field];
    if (JSON.stringify(was) === JSON.stringify(now)) continue;
    const label = UNIT_FIELD_LABELS[field] ?? field;
    if (LINE_FIELDS.has(field) && Array.isArray(was) && Array.isArray(now)) {
      const added = now.filter((line) => !was.includes(line));
      const removed = was.filter((line) => !now.includes(line));
      for (const line of added) lines.push(`${label}: added “${line}”`);
      for (const line of removed) lines.push(`${label}: removed “${line}”`);
      if (!added.length && !removed.length) lines.push(`${label}: reordered`);
      continue;
    }
    lines.push(`${label}: ${describeValue(was)} → ${describeValue(now)}`);
  }
  return lines.length ? lines : ["No change"];
}

/**
 * Each change with what it did. `changes` is newest first, as the history
 * endpoint sends it. `complete` says the list reaches back to the first
 * change; when it does not, the oldest change shown is compared with nothing
 * and is labelled as such rather than as "Created".
 */
export function describeHistory(
  changes: Change[],
  complete: boolean,
): { change: Change; lines: string[] }[] {
  return changes.map((change, index) => {
    // The previous change to the same record is the next older one with its subject.
    const previous = changes
      .slice(index + 1)
      .find((older) => older.subject.toLowerCase() === change.subject.toLowerCase());
    if (previous) return { change, lines: describeChange(change.snapshot, previous.snapshot) };
    if (!complete) return { change, lines: ["Earlier changes not shown"] };
    const first = change.subject === SHOWING_RULES_SUBJECT ? "First saved" : "Created";
    return { change, lines: [first] };
  });
}
