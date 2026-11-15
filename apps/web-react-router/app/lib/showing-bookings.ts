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
export type Channel = "sms" | "calendar_event" | "calendar_invite";

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
