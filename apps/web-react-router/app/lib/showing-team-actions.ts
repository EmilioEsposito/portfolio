// The team's actions on the Booking settings tab beside cancelling: a booking for someone who
// called, another host for a showing, and who came. Shapes from team_actions.py; the wording
// of each confirmation and each result lives here so it can be unit tested.
import {
  type AgentCalendar,
  type BookedPerson,
  type BookedShowing,
  type SendMode,
  type UnitOffers,
  describeOffer,
  formatWhen,
  humanize,
} from "./showing-bookings";

export type Attendance = "attended" | "no_show" | "unknown";

export interface QueuedNotice {
  id: number;
  kind: string;
  channel: string;
  status: string;
  /** Waits for a confirmation text to be delivered. */
  held: boolean;
}

export interface TeamBookingResult {
  showing_id: number;
  attendee_id: number;
  kind: "new" | "join";
  texts_accepted: boolean;
  confirmation: string | null;
  notices: QueuedNotice[];
}

export interface HostChangeResult {
  showing_id: number;
  host_id: number;
  host_name: string;
  previous_host_name: string;
  notices: QueuedNotice[];
}

export interface TeamBookingForm {
  unit: string;
  /** An offer's start, as the server sent it; empty until one is picked. */
  start: string;
  name: string;
  mobile: string;
  email: string;
  texts: boolean;
}

export function emptyBookingForm(units: Pick<UnitOffers, "unit">[]): TeamBookingForm {
  return { unit: units[0]?.unit ?? "", start: "", name: "", mobile: "", email: "", texts: false };
}

/** The unit's offers as options for the time picker: "Wed, Apr 14, 4:00 PM · Join, 2 places left". */
export function timeOptions(units: UnitOffers[], unit: string): { value: string; label: string }[] {
  const found = units.find((item) => item.unit === unit);
  return (found?.offers ?? []).map((offer) => ({
    value: offer.start,
    label: `${formatWhen(offer.start)} · ${describeOffer(offer)}`,
  }));
}

/** The body POST /showings/bookings takes. Texts are asked for only with a mobile. */
export function bookingBody(form: TeamBookingForm) {
  const mobile = form.mobile.trim();
  return {
    unit: form.unit,
    start: form.start,
    name: form.name.trim(),
    mobile,
    email: form.email.trim(),
    texts: form.texts && mobile !== "",
  };
}

/** The line for each field from a refusal's `{detail: {fields}}`; empty for any other answer. */
export function refusalFields(body: unknown): Record<string, string> {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (!detail || typeof detail !== "object" || Array.isArray(detail)) return {};
  const fields = (detail as { fields?: unknown }).fields;
  if (!fields || typeof fields !== "object" || Array.isArray(fields)) return {};
  return Object.fromEntries(
    Object.entries(fields as Record<string, unknown>).filter((entry): entry is [string, string] => typeof entry[1] === "string"),
  );
}

/** What is missing before the form can be sent; the server checks the rest. */
export function missingFields(form: TeamBookingForm): string[] {
  const missing: string[] = [];
  if (!form.unit) missing.push("a unit");
  if (!form.start) missing.push("a time");
  if (!form.name.trim()) missing.push("a name");
  return missing;
}

function modeNote(mode: SendMode): string {
  if (mode === "capture") return " Capture mode: it is kept under Messages, not sent.";
  if (mode === "off") return " Send mode is off: nothing goes out.";
  return "";
}

/**
 * The confirmation before a booking for a caller, or null when nothing reaches anyone outside
 * the team (a name alone: only the host's event).
 */
export function describeTeamBooking(form: TeamBookingForm, mode: SendMode): string | null {
  const body = bookingBody(form);
  if (!body.texts && !body.email) return null;
  const who = body.name || "The caller";
  const when = `${body.unit} on ${formatWhen(body.start)}`;
  const sends = body.texts
    ? body.email
      ? `${who} gets a confirmation text at ${body.mobile} now, then a calendar invite at ${body.email} once it is delivered.`
      : `${who} gets a confirmation text at ${body.mobile} now.`
    : `${who} gets a calendar invite at ${body.email} and no texts.`;
  return `Book ${who} for ${when}? ${sends}${modeNote(mode)}`;
}

const NOTICE_LABELS: Record<string, string> = {
  prospect_confirmation: "confirmation text",
  prospect_invite: "calendar invite",
  host_event: "host's event",
  host_event_update: "host's event update",
  host_event_moved: "old host's event cancelled",
  resident_new_showing: "residents' text",
};

const STATUS_WORDS: Record<string, string> = {
  pending: "goes with the next run",
  held: "waits for the confirmation text",
  captured: "captured",
  sent: "sent",
  failed: "failed",
  unknown: "may have gone out",
  skipped: "skipped",
  cancelled: "not sent",
};

/** "confirmation text (sent), host's event (waits for the confirmation text)". */
export function describeQueued(notices: QueuedNotice[]): string {
  if (notices.length === 0) return "Nothing was queued.";
  const parts = notices.map(
    (notice) =>
      `${NOTICE_LABELS[notice.kind] ?? humanize(notice.kind).toLowerCase()} (${STATUS_WORDS[notice.status] ?? notice.status})`,
  );
  return `Queued: ${parts.join(", ")}.`;
}

export function describeBookingResult(result: TeamBookingResult, form: TeamBookingForm): string {
  const joined = result.kind === "join" ? " into the showing already booked" : "";
  const texts = result.texts_accepted ? "" : " No texts.";
  return `Booked ${form.name.trim()} for ${form.unit} on ${formatWhen(form.start)}${joined}.${texts} ${describeQueued(result.notices)}`;
}

/** The agents who could take a showing over: active ones other than its host. */
export function hostChoices(
  agents: Pick<AgentCalendar, "agent_id" | "name" | "status">[],
  showing: Pick<BookedShowing, "host_id">,
): { id: number; name: string }[] {
  return agents
    .filter((agent) => agent.status !== "inactive" && agent.agent_id !== showing.host_id)
    .map((agent) => ({ id: agent.agent_id, name: agent.name }));
}

export function describeHostChange(result: HostChangeResult): string {
  return `${result.host_name} now hosts it instead of ${result.previous_host_name}. ${describeQueued(result.notices)}`;
}

export const ATTENDANCE_LABELS: Record<Attendance, string> = {
  attended: "Came",
  no_show: "Did not come",
  unknown: "Not marked",
};

/** Pressing the mark already set clears it; pressing the other one changes it. */
export function attendanceAfter(current: Attendance, pressed: Exclude<Attendance, "unknown">): Attendance {
  return current === pressed ? "unknown" : pressed;
}

export function describeAttendance(person: Pick<BookedPerson, "name">, attendance: Attendance): string {
  if (attendance === "unknown") return `Cleared the mark for ${person.name}.`;
  return `Marked ${person.name}: ${ATTENDANCE_LABELS[attendance].toLowerCase()}.`;
}
