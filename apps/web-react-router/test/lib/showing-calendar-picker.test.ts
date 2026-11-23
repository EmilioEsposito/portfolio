import assert from "node:assert/strict";
import test from "node:test";

import { newAgent } from "../../app/lib/showing-bookings.ts";
import {
  type AgentFormState,
  type CalendarCandidate,
  type CalendarCandidates,
  type ListStatus,
  MAX_CALENDARS,
  ROW_STATES,
  agentToSave,
  pickerGroups,
  pickerProblem,
  selectedCalendars,
  selectionLine,
  unreadableLine,
  withCalendars,
  withTick,
} from "../../app/lib/showing-calendar-picker.ts";

const ACCOUNT = "team@example.com";
const JANE = "jane.doe@example.com";
const SAM = "sam.lee@example.com";
const PEPPINO = "peppino@example.com";

const calendar = (
  calendar_id: string,
  name: string,
  group: CalendarCandidate["group"],
  readable: boolean | null = true,
): CalendarCandidate => ({ calendar_id, name, group, readable });

const list = (patch: Partial<CalendarCandidates> = {}): CalendarCandidates => ({
  connection: "usable",
  account: ACCOUNT,
  problem: null,
  directory: "read",
  calendar_list: "read",
  check_failed: false,
  truncated: false,
  calendars: [calendar(JANE, "Jane Doe", "team"), calendar(SAM, "Sam Lee", "team"), calendar(PEPPINO, PEPPINO, "shared")],
  ...patch,
});

const form = (patch: Partial<AgentFormState> = {}, email = ""): AgentFormState => ({
  id: "new",
  draft: { ...newAgent(), email },
  saved: [],
  followsEmail: true,
  calendars: list(),
  reading: false,
  readError: null,
  ...patch,
});

const editing = (saved: string[], patch: Partial<AgentFormState> = {}): AgentFormState =>
  form({
    id: 7,
    draft: { ...newAgent(), name: "Jane Doe", email: JANE, busy_calendar_ids: saved },
    saved,
    followsEmail: false,
    ...patch,
  });

const withEmail = (state: AgentFormState, email: string): AgentFormState => ({ ...state, draft: { ...state.draft, email } });

const ids = (state: AgentFormState) => pickerGroups(state).flatMap((group) => group.rows.map((row) => row.calendar_id));
const ticked = (state: AgentFormState) =>
  pickerGroups(state).flatMap((group) => group.rows.filter((row) => row.ticked).map((row) => row.calendar_id));

// The tick that follows the email

test("a new agent's own calendar is ticked when it is listed, and follows the email", () => {
  const open = form();
  assert.deepEqual(selectedCalendars(open), []);
  assert.deepEqual(selectedCalendars(withEmail(open, "jane.doe@example.co")), []);
  assert.deepEqual(selectedCalendars(withEmail(open, ` ${JANE.toUpperCase()} `)), [JANE]);
  assert.deepEqual(selectedCalendars(withEmail(open, SAM)), [SAM]);
  assert.deepEqual(selectedCalendars(withEmail(open, PEPPINO)), [PEPPINO]);
  assert.deepEqual(selectedCalendars(withEmail(open, "someone.else@example.com")), []);
  // A calendar that cannot be read is never ticked for them.
  const unreadable = list({ calendars: [calendar(JANE, "Jane Doe", "team", false)] });
  assert.deepEqual(selectedCalendars(form({ calendars: unreadable }, JANE)), []);
  // Nothing is ticked before the list is here.
  assert.deepEqual(selectedCalendars(form({ calendars: null }, JANE)), []);
});

test("a tick changed by hand ends the following, whichever way it changes", () => {
  const unticked = withTick(form({}, JANE), JANE, false);
  assert.deepEqual(selectedCalendars(unticked), []);
  assert.deepEqual(selectedCalendars(withEmail(unticked, SAM)), []);

  const added = withTick(form({}, JANE), PEPPINO, true);
  assert.deepEqual(selectedCalendars(added), [JANE, PEPPINO]);
  assert.deepEqual(selectedCalendars(withEmail(added, SAM)), [JANE, PEPPINO]);
});

test("editing shows the saved ticks and never follows the email", () => {
  const state = editing([SAM]);
  assert.deepEqual(selectedCalendars(state), [SAM]);
  assert.deepEqual(selectedCalendars(withEmail(state, PEPPINO)), [SAM]);
  assert.deepEqual(selectedCalendars(editing([])), []);
});

test("ticks are matched in any case and kept as they were saved", () => {
  const state = editing(["Jane.Doe@Example.com"]);
  assert.deepEqual(ticked(state), [JANE]);
  assert.equal(pickerGroups(state).length, 2);
  assert.deepEqual(agentToSave(state).busy_calendar_ids, ["Jane.Doe@Example.com"]);
  assert.deepEqual(agentToSave(withTick(state, SAM, true)).busy_calendar_ids, ["Jane.Doe@Example.com", SAM]);
  assert.deepEqual(agentToSave(withTick(state, JANE, false)).busy_calendar_ids, []);
  assert.deepEqual(agentToSave(withTick(state, JANE, true)).busy_calendar_ids, [JANE]);
});

test("at most ten calendars can be ticked", () => {
  const many = Array.from({ length: MAX_CALENDARS }, (_, at) => `c${at}@example.com`);
  const full = editing(many);
  assert.deepEqual(selectedCalendars(withTick(full, JANE, true)), many);
  assert.deepEqual(selectedCalendars(withTick(full, many[3], false)).length, 9);
  assert.equal(MAX_CALENDARS, 10);
});

// Nothing ticked is dropped

test("a saved calendar the list does not have stays in it, ticked, and is saved again", () => {
  const states: (CalendarCandidates | null)[] = [
    null,
    list({ calendars: [] }),
    list({ connection: "none", account: null, directory: null, calendar_list: null, calendars: [] }),
    list({ directory: "not_granted", calendar_list: "not_granted", calendars: [] }),
    list({ check_failed: true, calendars: [] }),
  ];
  for (const calendars of states) {
    const state = editing(["elsewhere@example.com", SAM], { calendars });
    assert.deepEqual(ticked(state).sort(), ["elsewhere@example.com", SAM].sort());
    assert.deepEqual(agentToSave(state).busy_calendar_ids, ["elsewhere@example.com", SAM]);
  }
});

test("a saved calendar that cannot be read stays in its group, ticked, and says so", () => {
  const state = editing([SAM, "gone@example.com", "elsewhere@example.com"], {
    calendars: list({
      calendars: [
        calendar(JANE, "Jane Doe", "team"),
        calendar(SAM, "Sam Lee", "team", false),
        calendar("gone@example.com", "gone@example.com", "saved", false),
        calendar("elsewhere@example.com", "elsewhere@example.com", "saved"),
      ],
    }),
  });

  const [team, shared, other] = pickerGroups(state);
  assert.deepEqual(
    team.rows.map((row) => [row.name, row.address, row.state, row.ticked]),
    [
      ["Jane Doe", JANE, "readable", false],
      ["Sam Lee", SAM, "unreadable", true],
    ],
  );
  assert.deepEqual(shared.rows, []);
  assert.equal(other.heading, "Other calendars");
  // Known by the address alone, so it is shown once.
  assert.deepEqual(
    other.rows.map((row) => [row.name, row.address, row.state, row.ticked]),
    [
      ["gone@example.com", null, "unreadable", true],
      ["elsewhere@example.com", null, "readable", true],
    ],
  );
  assert.equal(ROW_STATES.unreadable, "Can't read: counts as busy");
  assert.deepEqual(agentToSave(state).busy_calendar_ids, [SAM, "gone@example.com", "elsewhere@example.com"]);
});

test("an unticked saved calendar stays listed, so it can be ticked again", () => {
  const state = withTick(editing(["elsewhere@example.com"], { calendars: null }), "elsewhere@example.com", false);
  assert.deepEqual(ids(state), ["elsewhere@example.com"]);
  assert.deepEqual(ticked(state), []);
  assert.deepEqual(ticked(withTick(state, "elsewhere@example.com", true)), ["elsewhere@example.com"]);
});

test("a tick survives a refresh that no longer lists its calendar", () => {
  const without = list({ calendars: [calendar(SAM, "Sam Lee", "team")] });
  // Ticked by hand.
  const byHand = withCalendars(withTick(form(), JANE, true), without);
  assert.deepEqual(selectedCalendars(byHand), [JANE]);
  assert.deepEqual(
    pickerGroups(byHand).map((group) => [group.key, group.rows.map((row) => row.state)]),
    [
      ["team", ["readable"]],
      ["shared", []],
      ["other", ["unchecked"]],
    ],
  );
  // Ticked by following the email.
  const followed = withCalendars(form({}, JANE), without);
  assert.deepEqual(selectedCalendars(followed), [JANE]);
  assert.deepEqual(ticked(followed), [JANE]);
  assert.deepEqual(agentToSave(followed).busy_calendar_ids, [JANE]);
});

test("a new answer replaces the list and clears the trouble, and the tick goes on following", () => {
  const state = withCalendars(form({ calendars: null, reading: true, readError: "Network error" }, JANE), list());
  assert.deepEqual([state.reading, state.readError, state.followsEmail], [false, null, true]);
  assert.deepEqual(selectedCalendars(state), [JANE]);
  assert.deepEqual(selectedCalendars(withEmail(state, SAM)), [SAM]);
});

test("an agent is always saved to be read through the team's connection", () => {
  const state = editing([SAM]);
  const saved = agentToSave({ ...state, draft: { ...state.draft, connection_id: 4, name: " Jane Doe " } });
  assert.equal(saved.connection_id, null);
  assert.equal(saved.name, "Jane Doe");
});

// Groups and notes

test("calendars are grouped under the team and what is shared with the account", () => {
  const groups = pickerGroups(form());
  assert.deepEqual(
    groups.map((group) => [group.heading, group.rows.map((row) => row.name), group.note]),
    [
      ["Sernia team", ["Jane Doe", "Sam Lee"], null],
      ["Shared with team@example.com", [PEPPINO], null],
    ],
  );
  assert.equal(groups[1].rows[0].address, null);
});

test("a list that could not be read says which it is and leaves the other shown", () => {
  const notes: Record<Exclude<ListStatus, "read">, RegExp> = {
    not_granted: /not allowed to list the team yet\. Press Reconnect/,
    api_off: /People API is off/,
    sharing_off: /does not share its directory with apps.*External Directory Sharing/,
    refused: /refused to list the team/,
    failed: /Press Refresh to try again/,
  };
  for (const [status, words] of Object.entries(notes)) {
    const shown = list({ directory: status as ListStatus, calendars: [calendar(PEPPINO, PEPPINO, "shared")] });
    const [team, shared] = pickerGroups(form({ calendars: shown }));
    assert.match(team.note ?? "", words);
    assert.deepEqual(shared.rows.length, 1);
    assert.equal(shared.note, null);
    assert.equal(pickerProblem(shown), null);
  }
  const sharedNotes: Record<Exclude<ListStatus, "read">, RegExp> = {
    not_granted: /not allowed to list the calendars shared with team@example\.com yet\. Press Reconnect/,
    api_off: /Google Calendar API is off/,
    sharing_off: /refused to list the calendars shared with team@example\.com/,
    refused: /refused to list the calendars shared with team@example\.com/,
    failed: /Press Refresh to try again/,
  };
  for (const [status, words] of Object.entries(sharedNotes)) {
    const shown = list({ calendar_list: status as ListStatus, calendars: [calendar(JANE, "Jane Doe", "team")] });
    const [team, shared] = pickerGroups(form({ calendars: shown }));
    assert.match(shared.note ?? "", words);
    assert.equal(team.note, null);
    assert.equal(team.rows.length, 1);
    assert.equal(pickerProblem(shown), null);
  }
});

test("an empty group says so, and is left out when nothing is known of it", () => {
  const empty = pickerGroups(form({ calendars: list({ calendars: [] }) }));
  assert.deepEqual(
    empty.map((group) => group.note),
    ["No team calendar can be read.", "No other calendar is shared yet."],
  );
  assert.deepEqual(pickerGroups(form({ calendars: list({ calendars: [], check_failed: true }) })), []);
  const failed = list({ calendars: [], check_failed: true, directory: "failed" });
  assert.deepEqual(
    pickerGroups(form({ calendars: failed })).map((group) => group.key),
    ["team"],
  );
});

test("without either permission one note says to reconnect, and only ticked calendars are listed", () => {
  const calendars = list({
    directory: "not_granted",
    calendar_list: "not_granted",
    calendars: [calendar(SAM, SAM, "saved")],
  });
  const problem = pickerProblem(calendars);
  assert.match(problem?.message ?? "", /Press Reconnect under Calendar connections/);
  assert.equal(problem?.retry, false);
  const groups = pickerGroups(editing([SAM], { calendars }));
  assert.deepEqual(
    groups.map((group) => [group.key, group.rows.map((row) => row.state)]),
    [["other", ["readable"]]],
  );
});

test("without a usable connection the form says so, still saves, and keeps its ticks", () => {
  const blank = { directory: null, calendar_list: null, calendars: [calendar(SAM, SAM, "saved", null)] };
  const cases: [Partial<CalendarCandidates>, RegExp][] = [
    [{ connection: "none", account: null }, /No Google account is connected.*Connect one/],
    [{ connection: "lost" }, /no longer accepts the connection to team@example\.com.*Press Reconnect/],
    [
      { connection: "unusable", problem: "SHOWING_TOKEN_KEY is not set here." },
      /team@example\.com cannot be used in this environment.*SHOWING_TOKEN_KEY is not set here\./,
    ],
  ];
  for (const [patch, words] of cases) {
    const calendars = list({ ...blank, ...patch });
    const problem = pickerProblem(calendars);
    assert.match(problem?.message ?? "", words);
    assert.match(problem?.message ?? "", /The agent can still be saved, and ticked calendars stay ticked\./);
    assert.equal(problem?.retry, false);
    const groups = pickerGroups(editing([SAM], { calendars }));
    assert.deepEqual(
      groups.map((group) => [group.key, group.rows.map((row) => [row.state, row.ticked])]),
      [["other", [["unchecked", true]]]],
    );
  }
});

test("a failed check can be tried again", () => {
  assert.equal(pickerProblem(list({ check_failed: true }))?.retry, true);
  assert.equal(pickerProblem(list()), null);
});

// The status line

test("the line under the list counts the ticks", () => {
  assert.equal(selectionLine(0), "No calendars selected. This agent counts as busy and gets no showings.");
  assert.equal(selectionLine(1), "1 calendar selected. An event on any selected calendar blocks that time.");
  assert.equal(selectionLine(2), "2 calendars selected. An event on any selected calendar blocks that time.");
  assert.equal(
    selectionLine(10),
    "10 calendars selected. An event on any selected calendar blocks that time. 10 is the most an agent can have.",
  );
});

test("a ticked calendar that cannot be read is said to stop every showing, an unticked one is not", () => {
  const calendars = list({ calendars: [calendar(SAM, "Sam Lee", "team", false)] });
  assert.match(unreadableLine(pickerGroups(editing([SAM], { calendars }))) ?? "", /gets no showings until every selected/);
  assert.equal(unreadableLine(pickerGroups(withTick(editing([SAM], { calendars }), SAM, false))), null);
  assert.equal(unreadableLine(pickerGroups(editing([JANE]))), null);
});
