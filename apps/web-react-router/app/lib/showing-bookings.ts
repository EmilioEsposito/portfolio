// Showing bookings as the /api/showing-bookings endpoints send and take them, and
// the small pieces of logic the Booking settings tab needs. Shapes mirror
// api/src/showing_bookings/schema.py and settings.py.

export const SEND_MODES = ["off", "capture", "live"] as const;
export type SendMode = (typeof SEND_MODES)[number];

export interface BookingSettings {
  send_mode: SendMode;
  live_units: string[];
  test_sends_enabled: boolean;
}

export interface SettingsView {
  settings: BookingSettings;
  environment: string; // Railway's environment name, or "local"
  is_production: boolean;
  test_recipients: string[]; // shortened: "•••-0150", "t…@example.com"
}

export type NoticeStatus = "pending" | "captured" | "sent" | "failed" | "unknown" | "skipped";
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
}

export const MODE_LABELS: Record<SendMode, string> = {
  off: "Off",
  capture: "Capture",
  live: "Live",
};

export const STATUS_LABELS: Record<NoticeStatus, string> = {
  pending: "Waiting",
  captured: "Captured",
  sent: "Sent",
  failed: "Failed",
  unknown: "Unknown",
  skipped: "Skipped",
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

export function cleanSettings(settings: BookingSettings): BookingSettings {
  return { ...settings, live_units: parseUnits(settings.live_units.join(",")) };
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
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => (item as { msg?: unknown })?.msg)
      .filter((msg): msg is string => typeof msg === "string")
      .map((msg) => msg.replace(/^Value error, /, ""));
    if (messages.length) return messages.join(" ");
  }
  return status ? `${fallback} (${status}).` : fallback;
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
