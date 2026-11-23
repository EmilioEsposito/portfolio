// The calendar picker on the showing agent form: which calendars block an agent's
// showings. Shapes mirror api/src/showing_bookings/calendar_candidates.py, which
// GET /api/showing-bookings/calendar/candidates answers with.

import { type ShowingAgent, cleanAgent, eachOnce } from "~/sites/team/lib/showing-bookings";

// How reading one of Google's two lists went (providers.py's ListStatus).
export type ListStatus = "read" | "not_granted" | "api_off" | "sharing_off" | "refused" | "failed";

export interface CalendarCandidate {
  calendar_id: string;
  name: string; // the address when there is no name
  // saved: on the agent, and in neither list.
  group: "team" | "shared" | "saved";
  // null when it could not be checked. Only a saved calendar is listed without being readable.
  readable: boolean | null;
}

export interface CalendarCandidates {
  connection: "usable" | "none" | "lost" | "unusable";
  // The connected Google account the calendars are read through.
  account: string | null;
  problem: string | null; // why this environment cannot use the connection
  // null when Google was not asked.
  directory: ListStatus | null;
  calendar_list: ListStatus | null;
  // Google did not say whether some calendars can be read.
  check_failed: boolean;
  truncated: boolean;
  calendars: CalendarCandidate[];
}

// agents.py's MAX_CALENDARS and calendar_candidates.py's MAX_CANDIDATES.
export const MAX_CALENDARS = 10;
export const MAX_LISTED = 500;

/** The agent form while it is open. */
export interface AgentFormState {
  id: number | "new";
  // What is being typed and, once followsEmail is off, the ticked calendars.
  draft: ShowingAgent;
  // The calendars the agent had when the form opened; they stay listed when unticked.
  saved: string[];
  // A new agent's tick follows the email field until a tick is changed by hand.
  followsEmail: boolean;
  // The last answer that arrived, kept on screen while a new one is read.
  calendars: CalendarCandidates | null;
  reading: boolean;
  readError: string | null;
}

export function sameCalendar(a: string, b: string): boolean {
  return a.trim().toLowerCase() === b.trim().toLowerCase();
}

/** The ticked calendars: the draft's, or for a new agent the listed one at the email's address. */
export function selectedCalendars(form: Pick<AgentFormState, "draft" | "followsEmail" | "calendars">): string[] {
  if (!form.followsEmail) return form.draft.busy_calendar_ids;
  const own = form.calendars?.calendars.find(
    (calendar) => calendar.readable && sameCalendar(calendar.calendar_id, form.draft.email),
  );
  return own ? [own.calendar_id] : [];
}

/** The form after a tick changed by hand, from which point the ticks are the draft's own. */
export function withTick(form: AgentFormState, calendarId: string, ticked: boolean): AgentFormState {
  const others = selectedCalendars(form).filter((id) => !sameCalendar(id, calendarId));
  if (ticked && others.length >= MAX_CALENDARS) return form;
  return {
    ...form,
    followsEmail: false,
    draft: { ...form.draft, busy_calendar_ids: ticked ? [...others, calendarId] : others },
  };
}

/** The form with a new answer. A tick that followed the email and is no longer listed stays ticked. */
export function withCalendars(form: AgentFormState, calendars: CalendarCandidates): AgentFormState {
  const before = selectedCalendars(form);
  const next = { ...form, calendars, reading: false, readError: null };
  if (before.length === 0 || selectedCalendars(next).length > 0) return next;
  return { ...next, followsEmail: false, draft: { ...form.draft, busy_calendar_ids: before } };
}

/** The agent to save. Its calendars are always read through the team's connection. */
export function agentToSave(form: AgentFormState): ShowingAgent {
  return cleanAgent({ ...form.draft, connection_id: null, busy_calendar_ids: selectedCalendars(form) });
}

export interface PickerRow {
  calendar_id: string;
  name: string;
  // null when the name is the address.
  address: string | null;
  state: "readable" | "unreadable" | "unchecked";
  ticked: boolean;
}

export interface PickerGroup {
  key: "team" | "shared" | "other";
  heading: string;
  rows: PickerRow[];
  // Why the group is short or empty.
  note: string | null;
}

const accountName = (account: string | null | undefined) => account ?? "the connected account";

function row(calendar: CalendarCandidate, selected: string[]): PickerRow {
  return {
    calendar_id: calendar.calendar_id,
    name: calendar.name,
    address: sameCalendar(calendar.name, calendar.calendar_id) ? null : calendar.calendar_id,
    state: calendar.readable === null ? "unchecked" : calendar.readable ? "readable" : "unreadable",
    ticked: selected.some((id) => sameCalendar(id, calendar.calendar_id)),
  };
}

function directoryNote(list: CalendarCandidates): string | null {
  switch (list.directory) {
    case "not_granted":
      return "The app is not allowed to list the team yet. Press Reconnect under Calendar connections above, and leave its box ticked on Google's screen.";
    case "api_off":
      return "The People API is off in the app's Google Cloud project, so the team cannot be listed. Turn it on there, then press Refresh.";
    case "sharing_off":
      return "The Workspace does not share its directory with apps, so the team cannot be listed. An admin can turn on External Directory Sharing in the Google Admin console.";
    case "refused":
      return "Google refused to list the team. Directory sharing may be off in the Google Admin console.";
    case "failed":
      return "Google did not list the whole team just now. Press Refresh to try again.";
    default:
      return null;
  }
}

function sharedNote(list: CalendarCandidates): string | null {
  const account = accountName(list.account);
  switch (list.calendar_list) {
    case "not_granted":
      return `The app is not allowed to list the calendars shared with ${account} yet. Press Reconnect under Calendar connections above, and leave its box ticked on Google's screen.`;
    case "api_off":
      return "The Google Calendar API is off in the app's Google Cloud project, so shared calendars cannot be listed. Turn it on there, then press Refresh.";
    case "sharing_off":
    case "refused":
      return `Google refused to list the calendars shared with ${account}.`;
    case "failed":
      return `Google did not list every calendar shared with ${account} just now. Press Refresh to try again.`;
    default:
      return null;
  }
}

/**
 * The rows to show, in groups. Every ticked or saved calendar is in one of them:
 * one that is in neither list, or that the last answer did not have, is under
 * Other calendars.
 */
export function pickerGroups(
  form: Pick<AgentFormState, "draft" | "saved" | "followsEmail" | "calendars">,
): PickerGroup[] {
  const list = form.calendars;
  const selected = selectedCalendars(form);
  const rows = (group: CalendarCandidate["group"]) =>
    (list?.calendars ?? []).filter((calendar) => calendar.group === group).map((calendar) => row(calendar, selected));
  const missing = eachOnce([...selected, ...form.saved])
    .filter((id) => !list?.calendars.some((calendar) => sameCalendar(calendar.calendar_id, id)))
    .map((id) => row({ calendar_id: id, name: id, group: "saved", readable: null }, selected));

  const groups: PickerGroup[] = [];
  // Without either permission there is one note for both, above the groups.
  if (list?.connection === "usable" && !lacksBothPermissions(list)) {
    const team = rows("team");
    const shared = rows("shared");
    groups.push({
      key: "team",
      heading: "Sernia team",
      rows: team,
      note: directoryNote(list) ?? (team.length || list.check_failed ? null : "No team calendar can be read."),
    });
    groups.push({
      key: "shared",
      heading: `Shared with ${accountName(list.account)}`,
      rows: shared,
      note: sharedNote(list) ?? (shared.length || list.check_failed ? null : "No other calendar is shared yet."),
    });
  }
  const other = [...rows("saved"), ...missing];
  if (other.length) groups.push({ key: "other", heading: "Other calendars", rows: other, note: null });
  // A group with nothing known about it is left out; the problem above the list says why.
  return groups.filter((group) => group.rows.length > 0 || group.note);
}

function lacksBothPermissions(list: CalendarCandidates): boolean {
  return list.directory === "not_granted" && list.calendar_list === "not_granted";
}

const STILL_SAVES = "The agent can still be saved, and ticked calendars stay ticked.";

/** What stands in the way of the whole list, and whether trying again may help. */
export function pickerProblem(list: CalendarCandidates): { message: string; retry: boolean } | null {
  const account = accountName(list.account);
  if (list.connection === "none") {
    return {
      message: `No Google account is connected, so calendars cannot be listed. Connect one under Calendar connections above. ${STILL_SAVES}`,
      retry: false,
    };
  }
  if (list.connection === "lost") {
    return {
      message: `Google no longer accepts the connection to ${account}, so calendars cannot be listed. Press Reconnect under Calendar connections above. ${STILL_SAVES}`,
      retry: false,
    };
  }
  if (list.connection === "unusable") {
    return {
      message: [`${account} cannot be used in this environment, so calendars cannot be listed.`, list.problem, STILL_SAVES]
        .filter(Boolean)
        .join(" "),
      retry: false,
    };
  }
  if (list.check_failed) {
    return { message: "Google did not say which calendars can be read, so some may be missing here.", retry: true };
  }
  if (lacksBothPermissions(list)) {
    return {
      message:
        "The app is not allowed to list calendars yet. Press Reconnect under Calendar connections above, and leave every box ticked on Google's screen. Until then, calendars already ticked stay ticked.",
      retry: false,
    };
  }
  return null;
}

/** The line under the list. */
export function selectionLine(count: number): string {
  if (count === 0) return "No calendars selected. This agent counts as busy and gets no showings.";
  const line = `${count} ${count === 1 ? "calendar" : "calendars"} selected. An event on any selected calendar blocks that time.`;
  return count >= MAX_CALENDARS ? `${line} ${MAX_CALENDARS} is the most an agent can have.` : line;
}

/** Said when a ticked calendar cannot be read: the whole agent counts as busy then, not just that calendar. */
export function unreadableLine(groups: PickerGroup[]): string | null {
  const stuck = groups.some((group) => group.rows.some((item) => item.ticked && item.state === "unreadable"));
  return stuck ? "This agent gets no showings until every selected calendar can be read." : null;
}

export const ROW_STATES: Record<PickerRow["state"], string> = {
  readable: "Readable",
  unreadable: "Can't read: counts as busy",
  unchecked: "Not checked",
};
