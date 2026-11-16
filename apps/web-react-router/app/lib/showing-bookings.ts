// Showing bookings as the /api/showing-bookings endpoints send and take them, and
// the small pieces of logic the Booking settings tab needs. Shapes mirror
// api/src/showing_bookings/schema.py and settings.py.

export const SEND_MODES = ["off", "capture", "live"] as const;
export type SendMode = (typeof SEND_MODES)[number];

export interface BookingSettings {
  send_mode: SendMode;
  live_units: string[];
  test_sends_enabled: boolean;
  // The Zillow lead agent offers the open times computed here. Off by default.
  lead_agent_uses_open_times: boolean;
  // The public booking routes answer. null: the environment's default (off in
  // production, on elsewhere). Changed only through its own route; a send-mode
  // save keeps what is stored, as it does the limits.
  public_booking_enabled?: boolean | null;
  public_limits?: PublicLimits;
}

export interface PublicLimits {
  upcoming_per_mobile: number;
  upcoming_per_email: number;
  bookings_per_ip_per_day: number;
}

export interface SettingsView {
  settings: BookingSettings;
  environment: string; // Railway's environment name, or "local"
  is_production: boolean;
  test_recipients: string[]; // shortened: "•••-0150", "t…@example.com"
  // Whether the public booking routes answer here, and what unset means here.
  public_booking_on: boolean;
  public_booking_default: boolean;
}

// held: waiting for the prospect's confirmation text to be reported delivered.
// cancelled: never sent, because the booking was withdrawn or the text was not delivered.
export type NoticeStatus =
  | "pending"
  | "held"
  | "captured"
  | "sent"
  | "failed"
  | "unknown"
  | "skipped"
  | "cancelled";
export type Channel = "sms" | "calendar_event" | "calendar_invite" | "email";

export interface Notice {
  id: number;
  kind: string;
  channel: Channel;
  showing_id: number;
  attendee_id: number | null;
  unit: string;
  showing_starts_at: string;
  recipient: string;
  recipient_key: string;
  wording: string;
  payload: Record<string, unknown>;
  status: NoticeStatus;
  mode: SendMode | null;
  guard_reason: string | null;
  not_before: string;
  attempts: number;
  provider_status: string | null;
  last_error: string | null;
  created_at: string;
  delivered_at: string | null;
}

export interface DrainReport {
  captured: number;
  sent: number;
  failed: number;
  unknown: number;
  skipped: number;
  retrying: number;
  interrupted: number;
  // Held notices released or stopped before the drain ("Send pending now").
  released?: number;
  stopped?: number;
}

export const MODE_LABELS: Record<SendMode, string> = {
  off: "Off",
  capture: "Capture",
  live: "Live",
};

export const STATUS_LABELS: Record<NoticeStatus, string> = {
  pending: "Waiting",
  held: "Held",
  captured: "Captured",
  sent: "Sent",
  failed: "Failed",
  unknown: "Unknown",
  skipped: "Skipped",
  cancelled: "Cancelled",
};

export const CHANNEL_LABELS: Record<Channel, string> = {
  sms: "Text",
  calendar_event: "Host's calendar event",
  calendar_invite: "Calendar invite",
  email: "Email",
};

// Why the guard chose to skip, capture or send (guard.py's reasons). The
// status says what then happened, so these never claim an outcome.
const GUARD_REASONS: Record<string, string> = {
  send_mode_off: "Send mode off",
  send_mode_capture: "Send mode capture",
  live_unit: "Live unit in production",
  test_recipient: "Test recipients only",
  unit_not_live: "Unit not live",
  not_production: "Outside production",
};

export function describeGuard(reason: string | null): string {
  if (!reason) return "";
  return GUARD_REASONS[reason] ?? reason;
}

/** What the mode does in this environment, in a sentence or two. */
export function describeMode(mode: SendMode, view: Pick<SettingsView, "is_production">): string {
  if (mode === "off") return "Sends and keeps nothing. Each message is marked skipped.";
  if (mode === "capture") return "Keeps each message below and sends nothing.";
  if (!view.is_production) return "Sends messages for the live units in production. Here, only test sends go out.";
  return "Sends messages for the live units. Other units' messages are captured.";
}

/** "320-03, 12-A" or one unit per line, as a list: trimmed, without repeats in any case. */
export function parseUnits(text: string): string[] {
  const units: string[] = [];
  for (const piece of text.split(/[\s,]+/)) {
    const unit = piece.trim();
    if (unit && !units.some((seen) => seen.toLowerCase() === unit.toLowerCase())) units.push(unit);
  }
  return units;
}

/** The settings in one key order with units cleaned, so two that mean the same compare equal. */
export function cleanSettings(settings: BookingSettings): BookingSettings {
  return {
    send_mode: settings.send_mode,
    live_units: parseUnits(settings.live_units.join(",")),
    test_sends_enabled: settings.test_sends_enabled,
    lead_agent_uses_open_times: Boolean(settings.lead_agent_uses_open_times),
  };
}

export function sameSettings(a: BookingSettings, b: BookingSettings): boolean {
  return JSON.stringify(cleanSettings(a)) === JSON.stringify(cleanSettings(b));
}

/** Saving this would start real sends in production: worth a second look. */
export function startsLiveSends(saved: BookingSettings, next: BookingSettings, isProduction: boolean): boolean {
  if (!isProduction || next.send_mode !== "live") return false;
  if (saved.send_mode !== "live") return next.live_units.length > 0 || next.test_sends_enabled;
  const before = new Set(saved.live_units.map((unit) => unit.toLowerCase()));
  return next.live_units.some((unit) => !before.has(unit.toLowerCase())) || (next.test_sends_enabled && !saved.test_sends_enabled);
}

/** One line for the result of "Send pending now". */
export function describeDrain(report: DrainReport): string {
  const parts: string[] = [];
  const add = (count: number, words: string) => {
    if (count > 0) parts.push(`${count} ${words}`);
  };
  add(report.released ?? 0, "released from hold");
  add(report.stopped ?? 0, "stopped");
  add(report.sent, "sent");
  add(report.captured, "captured");
  add(report.skipped, "skipped");
  add(report.failed, "failed");
  add(report.unknown, "unknown");
  add(report.retrying, "to try again later");
  add(report.interrupted, "cut off midway, now unknown");
  return parts.length ? `${parts.join(", ")}.` : "Nothing was due.";
}

/** "prospect_confirmation" reads "Prospect confirmation". */
export function humanize(kind: string): string {
  const words = kind.replaceAll("_", " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

const LOCAL_TIME = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York",
  weekday: "short",
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
});

/** A time as Sernia's team reads it, in Eastern time: "Wed, Apr 14, 4:00 PM". */
export function formatWhen(iso: string | null): string {
  if (!iso) return "";
  const when = new Date(iso);
  return Number.isNaN(when.getTime()) ? iso : LOCAL_TIME.format(when);
}

export function needsAttention(status: NoticeStatus): boolean {
  return status === "failed" || status === "unknown";
}

/** The server's reason for refusing a request, or a fallback. */
export function refusalMessage(status: number, body: unknown, fallback: string): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    const message = (detail as { message?: unknown }).message;
    if (typeof message === "string") return message;
  }
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => (item as { msg?: unknown })?.msg)
      .filter((msg): msg is string => typeof msg === "string")
      .map((msg) => msg.replace(/^Value error, /, ""));
    if (messages.length) return messages.join(" ");
  }
  return status ? `${fallback} (${status}).` : fallback;
}

/** A failed check's reasons, from `{detail: {message, problems}}`; empty for any other refusal. */
export function refusalProblems(body: unknown): string[] {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (!detail || typeof detail !== "object" || Array.isArray(detail)) return [];
  const problems = (detail as { problems?: unknown }).problems;
  return Array.isArray(problems) ? problems.map(String) : [];
}

// Calendar connections, as /api/showing-bookings/calendar/* sends them
// (routes.py's ConnectionView and ConnectionsView).

export type ConnectionStatus = "connected" | "lost" | "revoked";

export interface CalendarConnection {
  id: number;
  google_email: string;
  status: ConnectionStatus;
  has_showings_calendar: boolean;
  connected_by: string; // the Sernia email of whoever connected it
  connected_at: string;
  last_read_at: string | null;
  last_read_ok: boolean | null; // null until the first read
  last_error: string | null;
  // False when this environment cannot use it: lost or disconnected, no Google
  // client here, or no key to read its token (as in a preview).
  usable_here: boolean;
  unusable_reason: string | null;
}

export interface ConnectionsView {
  can_connect: boolean;
  setup_problem: string | null;
  connections: CalendarConnection[];
}

export const CONNECTION_STATUS_LABELS: Record<ConnectionStatus, string> = {
  connected: "Connected",
  lost: "Lost",
  revoked: "Disconnected",
};

// What Google's callback sends back in ?calendar= (connections.py's ConnectResult).
export const CONNECT_RESULTS = {
  connected: {
    ok: true,
    message: "Google account connected. The app can read its free and busy times, and its Sernia showings calendar is ready.",
  },
  denied: { ok: false, message: "Nothing was connected: the request was cancelled on Google's screen." },
  missing_permissions: {
    ok: false,
    message: "Nothing was connected: both calendar permissions are needed. Connect again and leave both ticked on Google's screen.",
  },
  expired: { ok: false, message: "Nothing was connected: it took more than ten minutes. Start again." },
  invalid: { ok: false, message: "Nothing was connected: the link back from Google was not valid. Start again from this tab." },
  not_configured: { ok: false, message: "Nothing was connected: calendar connections are not set up in this environment." },
  failed: { ok: false, message: "Nothing was connected: Google did not complete the connection. Try again." },
} as const satisfies Record<string, { ok: boolean; message: string }>;

export type ConnectResult = keyof typeof CONNECT_RESULTS;

/** The callback's result from the address, or null for none or one this tab does not know. */
export function connectResult(code: string | null): { code: ConnectResult; ok: boolean; message: string } | null {
  if (!code || !Object.hasOwn(CONNECT_RESULTS, code)) return null;
  const known = code as ConnectResult;
  return { code: known, ...CONNECT_RESULTS[known] };
}

/** One line on how the last free/busy read went. */
export function describeLastRead(connection: Pick<CalendarConnection, "last_read_at" | "last_read_ok">): string {
  if (!connection.last_read_at || connection.last_read_ok === null) return "Not read yet.";
  const when = formatWhen(connection.last_read_at);
  return connection.last_read_ok ? `Last read ${when}: fine.` : `Last read ${when}: a problem.`;
}

/** Which buttons a connection gets. Reconnect saves a new token, so it also mends one unusable here. */
export function connectionActions(connection: Pick<CalendarConnection, "status" | "usable_here">, canConnect: boolean) {
  return {
    check: connection.status === "connected" && connection.usable_here,
    reconnect: canConnect,
    // Disconnect asks Google to end the grant first; where the token cannot be
    // read, the server refuses and says why.
    disconnect: connection.status !== "revoked",
  };
}

// Showing agents, as /api/showing-bookings/agents sends and takes them
// (schema.py's ShowingAgent and routes.py's AgentView).

export interface Window {
  days: ("mon" | "tue" | "wed" | "thu" | "fri" | "sat" | "sun")[];
  start: string; // "HH:MM" or "HH:MM:SS", local to the showing rules' time zone
  end: string; // the last start time, included
}

export interface ShowingAgent {
  name: string;
  email: string;
  mobile: string | null;
  // The connection that reads this agent's calendars; null means the team's.
  connection_id: number | null;
  busy_calendar_ids: string[];
  weekly_hours: Window[];
  preferred_windows: Window[];
  priority: number;
  active: boolean;
}

export interface AgentView extends ShowingAgent {
  id: number;
  // The account that reads this agent's calendars, or null when none is connected.
  reads_through: string | null;
}

export function newAgent(): ShowingAgent {
  return {
    name: "",
    email: "",
    mobile: null,
    connection_id: null,
    busy_calendar_ids: [],
    weekly_hours: [],
    preferred_windows: [],
    priority: 1,
    active: true,
  };
}

/** Calendar addresses typed one per line or separated by commas: trimmed, each once. */
export function parseCalendars(text: string): string[] {
  const found: string[] = [];
  for (const piece of text.split(/[\s,;]+/)) {
    const value = piece.trim();
    if (value && !found.some((seen) => seen.toLowerCase() === value.toLowerCase())) found.push(value);
  }
  return found;
}

function shortClock(value: string): string {
  return value.slice(0, 5);
}

/** The agent as it should be sent: text trimmed, empty mobile as null, times in one form. */
export function cleanAgent(agent: ShowingAgent): ShowingAgent {
  const windows = (list: Window[]) =>
    list.map((window) => ({ days: window.days, start: shortClock(window.start), end: shortClock(window.end) }));
  return {
    ...agent,
    name: agent.name.trim(),
    email: agent.email.trim(),
    mobile: agent.mobile?.trim() ? agent.mobile.trim() : null,
    busy_calendar_ids: parseCalendars(agent.busy_calendar_ids.join("\n")),
    weekly_hours: windows(agent.weekly_hours),
    preferred_windows: windows(agent.preferred_windows),
  };
}

const DAY_NAMES: Record<Window["days"][number], string> = {
  mon: "Mon",
  tue: "Tue",
  wed: "Wed",
  thu: "Thu",
  fri: "Fri",
  sat: "Sat",
  sun: "Sun",
};

/** "4:00 PM" from "16:00" or "16:00:00". */
export function clock(value: string): string {
  const [hours, minutes] = value.split(":").map(Number);
  const suffix = hours >= 12 ? "PM" : "AM";
  const twelve = hours % 12 === 0 ? 12 : hours % 12;
  return `${twelve}:${String(minutes).padStart(2, "0")} ${suffix}`;
}

/** "Mon/Wed 4:00 PM–6:30 PM", with the last start time, as windows are saved. */
export function describeWindows(windows: Window[], empty: string): string {
  if (!windows.length) return empty;
  return windows
    .map((window) => `${window.days.map((day) => DAY_NAMES[day]).join("/")} ${clock(window.start)}–${clock(window.end)}`)
    .join(", ");
}

// Open times and showings, as GET /api/showing-bookings/showings sends them
// (open_times.py's OpenTimes and routes.py's ShowingsView).

export interface Offer {
  start: string;
  kind: "join" | "new";
  preferred: boolean;
  places_left: number;
  host_id: number;
  host_name: string;
  showing_id: number | null;
}

export interface UnitOffers {
  unit: string;
  address: string;
  notice_hours: number;
  offers: Offer[];
  why_none: string | null;
}

export type CalendarStatus = "read" | "sample" | "unreadable" | "inactive";

export interface AgentCalendar {
  agent_id: number;
  name: string;
  status: CalendarStatus;
  connection_email: string | null;
  problem: string | null;
}

export interface OpenTimes {
  now: string;
  timezone: string;
  units: UnitOffers[];
  agents: AgentCalendar[];
  sample_calendar_in_use: boolean;
}

export interface BookedPerson {
  id: number;
  name: string;
  /** "412-555-0117", or "no mobile". */
  mobile: string;
  email: string | null;
  source: "page" | "team" | "lead_agent";
  /** False for someone booked without texts: a cancellation texts them nothing. */
  texts_accepted: boolean;
  /** Set when the booking needs a call, such as "confirmation_undelivered". */
  attention_reason: string | null;
  /** Whether they came; "unknown" until marked after the start. */
  attendance: "attended" | "no_show" | "unknown";
}

export interface BookedShowing {
  id: number;
  unit: string;
  starts_at: string;
  host_id: number;
  host_name: string;
  attendees: number;
  capacity: number;
  people: BookedPerson[];
  /** A started showing can no longer be cancelled. */
  started: boolean;
  /** Why the unit's residents were not texted about it, such as "lookup_failed". */
  resident_attention: string | null;
}

export interface ShowingsView {
  open_times: OpenTimes;
  booked: BookedShowing[];
  lead_agent_uses_open_times: boolean;
  send_mode: SendMode;
}

export const ATTENTION_LABELS: Record<string, string> = {
  confirmation_undelivered: "Confirmation text not delivered: call them",
  confirmation_refused: "Quo refused the number: call them",
  confirmation_not_sent: "Confirmation text not sent: call them",
};

/** Why a booking needs the team, in words; any reason not listed is shown humanized. */
export function describeAttention(reason: string): string {
  return ATTENTION_LABELS[reason] ?? `${humanize(reason)}: call them`;
}

export const RESIDENT_ATTENTION_LABELS: Record<string, string> = {
  lookup_failed: "Could not read who lives here: tell the resident yourself",
  lease_dates_missing: "A contact at this unit has no lease dates: check who lives here and tell them yourself",
  unit_key_unreadable: "The unit key does not read as property-unit: tell the resident yourself",
  unit_spelled_differently: "A tenant's contact spells this unit differently: fix it, then tell the resident yourself",
  resident_without_mobile: "A current tenant has no mobile on their contact: tell them yourself",
  too_many_residents: "More residents than one group text takes: tell them yourself",
};

/** Why a showing's residents were not texted, in words. */
export function describeResidentAttention(reason: string): string {
  return RESIDENT_ATTENTION_LABELS[reason] ?? `Residents not texted (${humanize(reason)}): tell them yourself`;
}

/** Everyone a notice reaches: its recipient, and the other numbers of a group text. */
export function noticeRecipients(notice: Pick<Notice, "recipient" | "payload">): string {
  const others = notice.payload?.also_to;
  return Array.isArray(others) && others.length > 0 ? [notice.recipient, ...others].join(", ") : notice.recipient;
}

/** A booking whose confirmation never reached the number: a cancellation does not text it again. */
const WRONG_NUMBER = new Set(["confirmation_undelivered", "confirmation_refused"]);

function texted(people: Pick<BookedPerson, "texts_accepted" | "mobile" | "attention_reason">[]): number {
  return people.filter(
    (person) =>
      person.texts_accepted && person.mobile !== "no mobile" && !WRONG_NUMBER.has(person.attention_reason ?? ""),
  ).length;
}

function modeNote(mode: SendMode): string {
  if (mode === "capture") return " Capture mode: these are kept under Messages, not sent.";
  if (mode === "off") return " Send mode is off: none of these go out.";
  return "";
}

/** The confirmation before the team cancels a whole showing: what it changes and sends. */
export function describeShowingCancel(
  showing: Pick<BookedShowing, "unit" | "starts_at" | "host_name" | "people">,
  mode: SendMode,
): string {
  const count = showing.people.length;
  const people = count === 1 ? "the 1 person booked" : `the ${count} people booked`;
  const texts = texted(showing.people);
  const told =
    texts === count
      ? "Each gets a cancellation text"
      : `${texts} of them get a cancellation text`;
  return (
    `Cancel the showing of ${showing.unit} on ${formatWhen(showing.starts_at)} for ${people}? ` +
    `${told} with a link to the units open for showings, their calendar invites are withdrawn, ` +
    `and ${showing.host_name}'s event is cancelled.${modeNote(mode)}`
  );
}

/** The confirmation before the team removes one person. */
export function describeRemoval(
  showing: Pick<BookedShowing, "unit" | "starts_at" | "host_name" | "people">,
  person: Pick<BookedPerson, "name" | "texts_accepted" | "mobile" | "attention_reason">,
  mode: SendMode,
): string {
  const last = showing.people.length <= 1;
  const text = texted([person]) ? `${person.name} gets a cancellation text, ` : `${person.name} gets no text, `;
  const host = last
    ? `and the showing is cancelled, with ${showing.host_name}'s event.`
    : `and ${showing.host_name}'s event is updated.`;
  return (
    `Remove ${person.name} from the showing of ${showing.unit} on ${formatWhen(showing.starts_at)}? ` +
    `${text}their calendar invite is withdrawn, ${host}${modeNote(mode)}`
  );
}

export const CALENDAR_STATUS_LABELS: Record<CalendarStatus, string> = {
  read: "Calendar read",
  sample: "Sample calendar",
  unreadable: "Counts as busy",
  inactive: "Inactive",
};

/** What kind of offer this is, in a few words: "Join, 2 places left" or "Preferred". */
export function describeOffer(offer: Pick<Offer, "kind" | "preferred" | "places_left">): string {
  if (offer.kind === "join") {
    return `Join, ${offer.places_left} ${offer.places_left === 1 ? "place" : "places"} left`;
  }
  return offer.preferred ? "Preferred" : "Open";
}

/** How many of a unit's offers to show before "Show all". */
export const OFFERS_SHOWN = 8;
