// Showing bookings as the /api/showing-bookings endpoints send and take them, and
// the small pieces of logic the Booking settings tab needs. Shapes mirror
// api/src/showing_bookings/schema.py and settings.py.

export const SEND_MODES = ["test", "capture", "live"] as const;
export type SendMode = (typeof SEND_MODES)[number];
// The mode a message was delivered in. "off" is only on messages from when it was a mode.
export type NoticeMode = SendMode | "off";

export interface BookingSettings {
  send_mode: SendMode;
  live_units: string[];
  // Every unit is live, including ones added later; the ticks are kept.
  live_all_units: boolean;
  // The Zillow lead agent offers the open times computed here. Off by default.
  lead_agent_uses_open_times: boolean;
  // The public booking routes answer. null, until it is changed: the environment's
  // default (off in production, on elsewhere). Changed only through its own route; a
  // send-mode save keeps what is stored, as it does the limits.
  public_booking_enabled?: boolean | null;
  public_limits?: PublicLimits;
}

export interface ListedUnit {
  unit: string;
  address: string;
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
  // Whether the public booking routes answer here.
  public_booking_on: boolean;
  // Every rental listing, for the units that can be live.
  listings: ListedUnit[];
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

// Why a notice needs the team (alerts.py): it failed and will not be tried
// again, or it has not gone out ten minutes after it was due.
export type NoticeAttention = "failed" | "stuck";

export interface Notice {
  id: number;
  kind: string;
  // What the notice is, in words, such as "Prospect's reminder".
  kind_label: string;
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
  mode: NoticeMode | null;
  guard_reason: string | null;
  // Who sent a captured message by hand, and when.
  send_approved_by?: string | null;
  send_approved_at?: string | null;
  not_before: string;
  attempts: number;
  provider_status: string | null;
  last_error: string | null;
  created_at: string;
  delivered_at: string | null;
  attention: NoticeAttention | null;
  // When the team was alerted about it, once.
  alerted_at: string | null;
}

export interface DrainReport {
  captured: number;
  sent: number;
  failed: number;
  unknown: number;
  skipped: number;
  retrying: number;
  interrupted: number;
  // Held notices released or stopped before the drain.
  released?: number;
  stopped?: number;
  // Reminders queued and team alerts raised by the same run.
  prospect_reminders?: number;
  resident_reminders?: number;
  lead_email_reminders?: number;
  alerts?: number;
}

export const MODE_LABELS: Record<NoticeMode, string> = {
  off: "Off",
  test: "Test",
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

// Why the guard chose to capture or send (guard.py's reasons). The status says
// what then happened, so these never claim an outcome.
const GUARD_REASONS: Record<string, string> = {
  send_mode_off: "Send mode off",
  send_mode_capture: "Send mode capture",
  live_unit: "Live unit in production",
  all_units_live: "All units live in production",
  test_recipient: "Test list only",
  approved: "Sent by a person",
  unit_not_live: "Unit not live",
  not_test_recipient: "Not on the test list",
  not_production: "Outside production",
};

export function describeGuard(reason: string | null): string {
  if (!reason) return "";
  return GUARD_REASONS[reason] ?? reason;
}

export const MODE_TITLES: Record<SendMode, string> = {
  test: "Test mode",
  capture: "Capture mode",
  live: "Live mode",
};

export const MODE_DESCRIPTIONS: Record<SendMode, string> = {
  test: "Sends only to the test list. Messages to anyone else are captured.",
  capture: "Sends nothing by itself. Each message waits under Captured messages until you send it.",
  live: "Sends to real people at once, for the units ticked here.",
};

const sameUnit = (a: string, b: string) => a.toLowerCase() === b.toLowerCase();

/** A tick put on or taken off one unit, keeping the others as they are. */
export function tickUnit(units: string[], unit: string, ticked: boolean): string[] {
  const others = units.filter((each) => !sameUnit(each, unit));
  return ticked ? [...others, unit] : others;
}

export function isTicked(units: string[], unit: string): boolean {
  return units.some((each) => sameUnit(each, unit));
}

/** The units to tick: every rental listing, then any ticked one whose listing is gone, so it can be unticked. */
export function unitTicks(listings: ListedUnit[], ...ticked: string[][]): (ListedUnit & { listed: boolean })[] {
  const shown = listings.map((listing) => ({ ...listing, listed: true }));
  for (const unit of ticked.flat()) {
    if (!shown.some((each) => sameUnit(each.unit, unit))) shown.push({ unit, address: "", listed: false });
  }
  return shown;
}

/** The settings in one key order, with the ticks in any order, so two that mean the same compare equal. */
export function cleanSettings(settings: BookingSettings): BookingSettings {
  const units: string[] = [];
  for (const unit of settings.live_units) if (!isTicked(units, unit.trim())) units.push(unit.trim());
  return {
    send_mode: settings.send_mode,
    live_units: units.sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase())),
    live_all_units: Boolean(settings.live_all_units),
    lead_agent_uses_open_times: Boolean(settings.lead_agent_uses_open_times),
  };
}

export function sameSettings(a: BookingSettings, b: BookingSettings): boolean {
  return JSON.stringify(cleanSettings(a)) === JSON.stringify(cleanSettings(b));
}

/** "320-03", "320-03 and 412-01", or "320-03, 320-05 and 412-01". */
export function joinWords(words: string[]): string {
  if (words.length <= 1) return words.join("");
  return `${words.slice(0, -1).join(", ")} and ${words[words.length - 1]}`;
}

/** Whether a real person could be texted about this unit, when the settings are live in production. */
function unitIsLive(settings: BookingSettings, unit: string): boolean {
  return settings.live_all_units || isTicked(settings.live_units, unit);
}

/**
 * The question before a save that starts real sends in production, naming the
 * units; null when it starts none.
 */
export function liveStartQuestion(saved: BookingSettings, next: BookingSettings, isProduction: boolean): string | null {
  if (!isProduction || next.send_mode !== "live") return null;
  const before = saved.send_mode === "live" ? saved : { ...saved, live_units: [], live_all_units: false };
  if (next.live_all_units) {
    if (before.live_all_units) return null;
    return "This starts real texts and calendar invites for every unit, including ones added later. Save?";
  }
  if (before.live_all_units) return null;
  const started = next.live_units.filter((unit) => !unitIsLive(before, unit));
  if (started.length === 0) return null;
  return `This starts real texts and calendar invites for ${joinWords(started)}. Save?`;
}

/**
 * A line for someone taking live units away while the mode stays live; null when
 * none are. A unit with no tick keeps its showings, and their messages are captured.
 */
export function untickedNote(saved: BookingSettings, next: BookingSettings, listings: ListedUnit[]): string | null {
  if (saved.send_mode !== "live" || next.send_mode !== "live" || next.live_all_units) return null;
  const before = saved.live_all_units ? listings.map((listing) => listing.unit) : saved.live_units;
  const taken = before.filter((unit) => !isTicked(next.live_units, unit));
  if (taken.length === 0) return null;
  return `Showings already booked at ${joinWords(taken)} stay, and their messages are captured.`;
}

/** Who can book now, from the saved switches. The public cannot while the pages answer 404. */
function whoCanBook(view: SettingsView, pagesOn: boolean): string {
  const { settings } = view;
  // Live with no unit ticked closes every unit to the public and to leads.
  const open = settings.send_mode !== "live" || settings.live_all_units || settings.live_units.length > 0;
  const who = ["Your team"];
  if (open && view.public_booking_on && pagesOn) who.push("the public");
  if (open && settings.lead_agent_uses_open_times) who.push("Zillow leads");
  return `${joinWords(who)} can book.`;
}

/** What goes out, in a sentence, under these settings. */
export function whatGoesOut(settings: BookingSettings, view: Pick<SettingsView, "is_production" | "test_recipients">): string {
  if (settings.send_mode === "capture") return "Nothing is sent until you send it from Captured messages.";
  if (settings.send_mode === "test") {
    if (view.test_recipients.length === 0) return "No test list is set here, so nobody is texted. Everything is captured.";
    return "Only the test list is texted. Everything else is captured.";
  }
  if (!view.is_production) return "This is not production, so nobody is texted. Everything is captured.";
  if (settings.live_all_units) return "Real people are texted for every unit.";
  if (settings.live_units.length === 0) return "No unit is ticked, so nobody is texted.";
  return `Real people are texted for ${joinWords(settings.live_units)} only.`;
}

/** "Right now": who can book and what is sent, from the saved settings. */
export function rightNow(view: SettingsView, pagesOn: boolean): string {
  return `${whoCanBook(view, pagesOn)} ${whatGoesOut(view.settings, view)}`;
}

/** The same once these edits are saved. */
export function afterSaving(view: SettingsView, form: BookingSettings, pagesOn: boolean): string {
  return rightNow({ ...view, settings: { ...view.settings, ...form } }, pagesOn);
}

/**
 * What to add to a confirmation that says who gets which message, when they will
 * not really get it: the mode, and for live the unit.
 */
export function heldBackNote(settings: BookingSettings, isProduction: boolean, unit: string): string {
  if (settings.send_mode === "capture") return " Capture mode: these are kept under Captured messages, not sent.";
  if (settings.send_mode === "test") return " Test mode: only messages to the test list are sent. The rest are captured.";
  if (!isProduction) return " Outside production these are captured, not sent.";
  return unitIsLive(settings, unit) ? "" : ` ${unit} is not ticked for Live mode: these are captured, not sent.`;
}

// Captured messages, as GET /notices/captured and POST /notices/send-captured answer
// (captured.py).

export type CapturedState = "ready" | "too_late" | "no_longer_applies";

export interface CapturedNotice {
  id: number;
  kind: string;
  kind_label: string;
  channel: Channel;
  unit: string;
  showing_starts_at: string;
  // "Alex Parker, 412-555-0117".
  to: string;
  // As it would be sent now.
  wording: string;
  conversation_id: string | null;
  state: CapturedState;
  // Why it cannot be sent, for any state but ready.
  reason: string | null;
  // Why a ready one would wait, whatever else is sent with it.
  note: string | null;
  // The confirmation text it waits for when both are sent.
  waits_for_notice_id: number | null;
  confirmation_on_its_way: boolean;
  // Sending it really sends it: in production always, elsewhere only to the test list.
  goes_out: boolean;
  captured_at: string;
}

export type SendOutcome = "sent" | "waiting" | "unknown" | "not_sent" | "kept" | "handled";

export interface SendResult {
  id: number;
  kind_label: string;
  unit: string;
  showing_starts_at: string | null;
  to: string;
  outcome: SendOutcome;
  detail: string;
  waits_until: string | null;
}

export interface SendCapturedReport {
  results: SendResult[];
}

export const CAPTURED_STATE_LABELS: Record<CapturedState, string> = {
  ready: "Ready",
  too_late: "Too late",
  no_longer_applies: "No longer applies",
};

export const OUTCOME_LABELS: Record<SendOutcome, string> = {
  sent: "Sent",
  waiting: "Waiting",
  unknown: "May have gone out",
  not_sent: "Not sent",
  kept: "Still captured",
  handled: "Already handled",
};

/** The ready messages still ticked; ready ones are ticked until someone unticks them. */
export function pickedNotices(notices: CapturedNotice[], unticked: ReadonlySet<number>): CapturedNotice[] {
  return notices.filter((notice) => notice.state === "ready" && !unticked.has(notice.id));
}

/** Why a message is grayed, or why a ready one would wait with these ones ticked; null for one that goes at once. */
export function capturedLine(notice: CapturedNotice, picked: ReadonlySet<number>): string | null {
  if (notice.state !== "ready") return notice.reason;
  const waits = notice.confirmation_on_its_way || (notice.waits_for_notice_id !== null && picked.has(notice.waits_for_notice_id));
  const lines = [notice.note, waits ? "Goes once the confirmation is delivered." : null].filter(Boolean);
  return lines.length ? lines.join(" ") : null;
}

const messages = (count: number) => (count === 1 ? "1 message" : `${count} messages`);

/** The question before captured messages are sent: how many go to real people. */
export function sendQuestion(picked: CapturedNotice[], isProduction: boolean): string {
  if (isProduction) {
    return picked.length === 1
      ? "1 message goes to a real person. Once sent, it can't be taken back."
      : `${picked.length} messages go to real people. Once sent, they can't be taken back.`;
  }
  const out = picked.filter((notice) => notice.goes_out).length;
  const kept = picked.length - out;
  const none = "No message goes to real people: outside production only the test list can be reached.";
  if (out === 0) return `${none} ${picked.length === 1 ? "It stays" : "They stay"} captured.`;
  const list = `${messages(out)} ${out === 1 ? "goes" : "go"} to the test list.`;
  return kept === 0 ? `${none} ${list}` : `${none} ${list} ${messages(kept)} ${kept === 1 ? "stays" : "stay"} captured.`;
}

/** What happened to one message: "Waiting until Wed, Apr 14, 8:00 AM. Residents get texts only …". */
export function describeSendResult(result: SendResult): string {
  const label =
    result.outcome === "waiting" && result.waits_until
      ? `Waiting until ${formatWhen(result.waits_until)}`
      : OUTCOME_LABELS[result.outcome];
  return result.detail ? `${label}. ${result.detail}` : `${label}.`;
}

/** "2 sent, 1 waiting, 1 not sent." */
export function summarizeSend(results: SendResult[]): string {
  const order: SendOutcome[] = ["sent", "waiting", "unknown", "not_sent", "kept", "handled"];
  const words: Record<SendOutcome, string> = {
    sent: "sent",
    waiting: "waiting",
    unknown: "may have gone out",
    not_sent: "not sent",
    kept: "still captured",
    handled: "already handled",
  };
  const parts = order
    .map((outcome) => [results.filter((result) => result.outcome === outcome).length, words[outcome]] as const)
    .filter(([count]) => count > 0)
    .map(([count, word]) => `${count} ${word}`);
  return parts.length ? `${parts.join(", ")}.` : "Nothing was sent.";
}

/** One line for the result of one run of the every-minute job. */
export function describeDrain(report: DrainReport): string {
  const parts: string[] = [];
  const add = (count: number, words: string) => {
    if (count > 0) parts.push(`${count} ${words}`);
  };
  add(report.released ?? 0, "released from hold");
  add(report.stopped ?? 0, "stopped");
  add(
    (report.prospect_reminders ?? 0) + (report.resident_reminders ?? 0) + (report.lead_email_reminders ?? 0),
    "reminders queued",
  );
  add(report.sent, "sent");
  add(report.captured, "captured");
  add(report.skipped, "skipped");
  add(report.failed, "failed");
  add(report.unknown, "unknown");
  add(report.retrying, "to try again later");
  add(report.interrupted, "cut off midway, now unknown");
  add(report.alerts ?? 0, "flagged to the team");
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

export const ATTENTION_BADGES: Record<NoticeAttention, string> = {
  failed: "Failed: needs a look",
  stuck: "Stuck: needs a look",
};

/** The notices that need the team: failed or stuck, as the server says. */
export function noticesNeedingTeam(notices: Notice[]): Notice[] {
  return notices.filter((notice) => notice.attention !== null);
}

/** "2 messages need a look", or null when none do. */
export function describeNeedingTeam(notices: Notice[]): string | null {
  const count = noticesNeedingTeam(notices).length;
  if (count === 0) return null;
  return count === 1 ? "1 message needs a look." : `${count} messages need a look.`;
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
  // Reconnecting would add a permission the showing agent form's calendar picker uses.
  reconnect_adds_permissions: boolean;
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
    message:
      "Nothing was connected: the free/busy and Sernia showings calendar permissions are both needed. Connect again and leave every box ticked on Google's screen.",
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

/** Calendar addresses, each once in any case, as first spelled. */
export function eachOnce(calendars: string[]): string[] {
  return calendars.filter(
    (value, at) => value && calendars.findIndex((seen) => seen.toLowerCase() === value.toLowerCase()) === at,
  );
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
    busy_calendar_ids: eachOnce(agent.busy_calendar_ids),
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
  /** Booked by the lead agent from an email thread; the lead has not added their details yet. */
  details_pending?: boolean;
  /** The lead conversation whose reply booked the place. */
  lead_conversation_id?: string | null;
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
}

export const ATTENTION_LABELS: Record<string, string> = {
  confirmation_undelivered: "Confirmation text not delivered: call them",
  confirmation_refused: "Quo refused the number: call them",
  confirmation_not_sent: "Confirmation text not sent: call them",
  reply_uncertain: "The email reply that booked this may not have gone out: check the thread",
};

/**
 * How a place the lead agent booked from an email thread stands, or null for
 * any other booking: "Booked by email with the lead agent · details not added yet".
 */
export function leadAgentLine(person: Pick<BookedPerson, "source" | "details_pending">): string | null {
  if (person.source !== "lead_agent") return null;
  return `Booked by email with the lead agent · ${person.details_pending ? "details not added yet" : "details added"}`;
}

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

/**
 * The lead conversation an email in a Zillow thread answers (the reminder by
 * email), so the list can link to its thread; null for every other notice.
 */
export function noticeLeadConversation(notice: Pick<Notice, "channel" | "payload">): string | null {
  const id = notice.payload?.conversation_id;
  return notice.channel === "email" && typeof id === "string" && id ? id : null;
}

/** A booking whose confirmation never reached the number: a cancellation does not text it again. */
const WRONG_NUMBER = new Set(["confirmation_undelivered", "confirmation_refused"]);

function texted(people: Pick<BookedPerson, "texts_accepted" | "mobile" | "attention_reason">[]): number {
  return people.filter(
    (person) =>
      person.texts_accepted && person.mobile !== "no mobile" && !WRONG_NUMBER.has(person.attention_reason ?? ""),
  ).length;
}

/** The confirmation before the team cancels a whole showing: what it changes and sends (``heldBackNote``). */
export function describeShowingCancel(
  showing: Pick<BookedShowing, "unit" | "starts_at" | "host_name" | "people">,
  note: string,
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
    `and ${showing.host_name}'s event is cancelled.${note}`
  );
}

/** The confirmation before the team removes one person. */
export function describeRemoval(
  showing: Pick<BookedShowing, "unit" | "starts_at" | "host_name" | "people">,
  person: Pick<BookedPerson, "name" | "texts_accepted" | "mobile" | "attention_reason">,
  note: string,
): string {
  const last = showing.people.length <= 1;
  const text = texted([person]) ? `${person.name} gets a cancellation text, ` : `${person.name} gets no text, `;
  const host = last
    ? `and the showing is cancelled, with ${showing.host_name}'s event.`
    : `and ${showing.host_name}'s event is updated.`;
  return (
    `Remove ${person.name} from the showing of ${showing.unit} on ${formatWhen(showing.starts_at)}? ` +
    `${text}their calendar invite is withdrawn, ${host}${note}`
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
