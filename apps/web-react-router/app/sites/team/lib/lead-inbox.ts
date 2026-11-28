/**
 * The lead inbox: Zillow lead threads as the API reads them
 * (api/src/zillow_leads/inbox.py), and the words the list and the lead page
 * show for them. No React here, so test/lib/lead-inbox.test.ts can check it.
 */

export const LEADS_API = "/api/zillow-leads/threads";
/** The leads list, the first tab under /team/leads. */
export const LEADS_PATH = "/team/leads";
/** How the lead agent works, the last tab. */
export const LEADS_OVERVIEW_PATH = "/team/leads/overview";
const THREAD_PATH = "/team/leads/threads";

/** One lead's page. Calendar events and notifications link here, so keep the path. */
export function leadThreadPath(threadId: string): string {
  return `${THREAD_PATH}/${encodeURIComponent(threadId)}`;
}

/** Whether a path is the leads list or a lead's page, so the Leads tab shows as current on both. */
export function isLeadsPath(pathname: string): boolean {
  const path = pathname.replace(/\/+$/, "").toLowerCase();
  return path === LEADS_PATH || path.startsWith(`${THREAD_PATH}/`);
}

export type LeadStatus =
  | "draft_waiting"
  | "needs_team"
  | "handed_off"
  | "replied"
  | "draft_rejected"
  | "no_reply_needed"
  | "handled";

export interface ShowingSummary {
  state: "booked" | "held" | "cancelled";
  unit: string;
  starts_at: string;
  attendance?: string | null;
  cancelled_by?: string | null;
}

export interface LeadThreadRow {
  thread_id: string;
  first_name: string;
  name: string;
  subject: string;
  unit: string | null;
  address: string;
  email_count: number;
  latest_from_sernia: boolean;
  latest_at: string | null;
  snippet: string;
  activity_at: string;
  status: LeadStatus;
  waiting_since: string | null;
  showing: ShowingSummary | null;
}

export interface LeadThreadList {
  threads: LeadThreadRow[];
  has_more: boolean;
}

export interface FormFact {
  label: string;
  value: string;
}

export interface LeadEmail {
  from_sernia: boolean;
  sent_at: string;
  subject: string;
  text: string;
  form: FormFact[];
  sent_by: "inbox" | "automatic" | null;
}

export interface LeadShowing {
  attendee_id: number;
  showing_id: number;
  unit: string;
  address: string;
  starts_at: string;
  status: "booked" | "cancelled";
  past: boolean;
  booked_by: string;
  source: string;
  booked_at: string | null;
  details_added: boolean;
  cancelled_by: string | null;
  cancelled_at: string | null;
  attendance: string;
  showings_path: string;
}

export interface WaitingCard {
  conversation_id: string;
  tool_call_id: string;
  kind: "draft" | "needs_team";
  tool_name: string;
  args: Record<string, any>;
  since: string;
  chat_path: string;
}

export type RunResult =
  | "waiting"
  | "needs_team"
  | "handed_off"
  | "sent"
  | "sent_edited"
  | "sent_automatically"
  | "rejected"
  | "handled"
  | "no_reply"
  | "closed";

export interface AgentRun {
  conversation_id: string;
  chat_path: string;
  at: string;
  emails_seen: number;
  action: string;
  reason: string;
  question: string | null;
  team_notes: string[];
  result: RunResult;
  decided_by: string | null;
  decided_at: string | null;
  handed_to: string | null;
}

export interface BookingLink {
  unit: string;
  address: string;
  url: string | null;
  no_link_because: string | null;
}

export interface LeadThreadPage {
  thread: LeadThreadRow;
  gmail_url: string;
  phone: string | null;
  facts: FormFact[];
  emails: LeadEmail[];
  emails_read_at: string | null;
  showings: LeadShowing[];
  waiting: WaitingCard[];
  runs: AgentRun[];
  booking_links: BookingLink[];
  booking_links_problem: string | null;
  latest_conversation_id: string;
  handed_to: string | null;
}

/** The status as a few words, and whether it waits on the team. */
export const STATUS_LABELS: Record<LeadStatus, string> = {
  draft_waiting: "Draft waiting for approval",
  needs_team: "Needs the team",
  handed_off: "Handed to Sernia AI",
  replied: "Replied",
  draft_rejected: "Draft rejected",
  no_reply_needed: "No reply needed",
  handled: "Handled",
};

export function waitsOnTeam(status: LeadStatus): boolean {
  return status === "draft_waiting" || status === "needs_team";
}

const ZONE = "America/New_York";
const CLOCK = new Intl.DateTimeFormat("en-US", { timeZone: ZONE, hour: "numeric", minute: "2-digit" });
const DAY = new Intl.DateTimeFormat("en-US", { timeZone: ZONE, month: "short", day: "numeric" });
const DAY_AND_YEAR = new Intl.DateTimeFormat("en-US", { timeZone: ZONE, month: "numeric", day: "numeric", year: "2-digit" });
const WHEN = new Intl.DateTimeFormat("en-US", {
  timeZone: ZONE,
  weekday: "short",
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
});
const YEAR = new Intl.DateTimeFormat("en-US", { timeZone: ZONE, year: "numeric" });
const DATE_KEY = new Intl.DateTimeFormat("en-CA", { timeZone: ZONE, year: "numeric", month: "2-digit", day: "2-digit" });

function parse(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const when = new Date(iso);
  return Number.isNaN(when.getTime()) ? null : when;
}

/** A time as Gmail's list shows it, in Eastern time: "9:49 PM" today, "Nov 27" this year, "11/27/25" before. */
export function listTime(iso: string | null, now: Date = new Date()): string {
  const when = parse(iso);
  if (!when) return "";
  if (DATE_KEY.format(when) === DATE_KEY.format(now)) return CLOCK.format(when);
  if (YEAR.format(when) === YEAR.format(now)) return DAY.format(when);
  return DAY_AND_YEAR.format(when);
}

/** A time in full, in Eastern time: "Sun, Nov 29, 1:30 PM". */
export function fullTime(iso: string | null): string {
  const when = parse(iso);
  return when ? WHEN.format(when) : "";
}

/** How long something has waited: "5 min", "3 h", "2 days". */
export function waitedFor(iso: string | null, now: Date = new Date()): string {
  const when = parse(iso);
  if (!when) return "";
  const minutes = Math.max(0, Math.round((now.getTime() - when.getTime()) / 60000));
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} h`;
  return `${Math.round(hours / 24)} days`;
}

// Why a place was cancelled, keyed by the booking service's cancel reason.
const CANCELLED_BY: Record<string, string> = {
  prospect: "cancelled by the lead",
  team: "cancelled by the team",
  off_market: "cancelled: the unit went off the market",
  number_refused: "not booked: the mobile number could not be texted",
  no_attendees: "cancelled: no one was left in it",
};

export function cancelledBy(reason: string | null | undefined): string {
  return (reason && CANCELLED_BY[reason]) || "cancelled";
}

/** The thread's showing in a few words: "Booked Sun, Nov 29, 1:30 PM". */
export function showingLabel(showing: ShowingSummary): string {
  const when = fullTime(showing.starts_at);
  if (showing.state === "booked") return `Booked ${when}`;
  if (showing.state === "held") {
    if (showing.attendance === "attended") return `Toured ${when}`;
    if (showing.attendance === "no_show") return `No-show ${when}`;
    return `Showing was ${when}`;
  }
  return `Cancelled: ${when}`;
}

const SOURCES: Record<string, string> = {
  page: "on the booking page",
  team: "by the team",
  lead_agent: "by email, from a reply",
};

/** How a place was booked: "on the booking page". */
export function bookedThrough(source: string): string {
  return SOURCES[source] ?? source;
}

const ATTENDANCE: Record<string, string> = { attended: "Came", no_show: "Did not come" };

/** One showing's state: "Booked", "Came", "Cancelled by the lead". */
export function showingState(showing: Pick<LeadShowing, "status" | "past" | "attendance" | "cancelled_by">): string {
  if (showing.status === "cancelled") {
    const reason = cancelledBy(showing.cancelled_by);
    return `${reason.charAt(0).toUpperCase()}${reason.slice(1)}`;
  }
  if (showing.past) return ATTENDANCE[showing.attendance] ?? "Held, attendance not marked";
  return "Booked";
}

const ACTIONS: Record<string, string> = {
  reply: "Drafted a reply",
  no_reply: "Sent nothing",
  escalate: "Asked the team",
};

/** What a run did: "Drafted a reply". */
export function runAction(action: string): string {
  return ACTIONS[action] ?? action;
}

/** How a run ended, with who decided and when where known. */
export function runResult(run: Pick<AgentRun, "result" | "decided_by" | "decided_at">): string {
  const by = run.decided_by ? ` by ${run.decided_by}` : "";
  const at = run.decided_at ? `, ${fullTime(run.decided_at)}` : "";
  switch (run.result) {
    case "waiting":
      return "Waiting for approval";
    case "needs_team":
      return "Waiting for the team";
    case "handed_off":
      return `Handed to Sernia AI${by}${at}`;
    case "sent":
      return `Approved${by} and sent${at}`;
    case "sent_edited":
      return `Edited${by} and sent${at}`;
    case "sent_automatically":
      return `Sent automatically${at}`;
    case "rejected":
      return `Rejected${by}${at}`;
    case "handled":
      return `Marked handled${by}${at}`;
    case "no_reply":
      return "Nothing was owed";
    default:
      return "Closed";
  }
}

/** The facts the header shows first, in this order; the rest follow. */
const LEADING_FACTS = ["move in", "pets", "number of occupants"];

export function orderedFacts(facts: FormFact[]): FormFact[] {
  const rank = (fact: FormFact) => {
    const index = LEADING_FACTS.indexOf(fact.label.toLowerCase());
    return index < 0 ? LEADING_FACTS.length : index;
  };
  return [...facts].sort((a, b) => rank(a) - rank(b));
}

/** A fact's label as the header shows it: "Number of Occupants" reads "Occupants". */
export function factLabel(label: string): string {
  if (label.toLowerCase() === "number of occupants") return "Occupants";
  if (label.toLowerCase() === "number of bedrooms") return "Bedrooms";
  return label;
}

/** The API's refusal as a sentence. */
export function refusal(status: number, body: unknown, fallback: string): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string" && detail) return detail;
  if (status === 401 || status === 403) return "Sign in again with your Sernia account.";
  return status ? `${fallback} (${status}).` : `${fallback}. Check your connection.`;
}
