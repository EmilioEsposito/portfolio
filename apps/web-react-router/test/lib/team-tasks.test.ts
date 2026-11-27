import assert from "node:assert/strict";
import test from "node:test";
import {
  assigneeText,
  openCount,
  dueLabel,
  statusText,
  taskKey,
  withStatus,
  type TeamTask,
} from "../../app/sites/team/lib/team-tasks.ts";

const NEW_YORK = "America/New_York";
// Monday 23 November 2026, 2 pm in New York.
const NOW = new Date("2026-11-23T19:00:00Z");

test("a due date reads by calendar day in the reader's time zone", () => {
  assert.deepEqual(dueLabel(null, NOW, NEW_YORK), { text: "No due date", tone: "none" });
  assert.deepEqual(dueLabel("not a date", NOW, NEW_YORK), { text: "No due date", tone: "none" });
  assert.deepEqual(dueLabel("2026-11-23T09:00:00Z", NOW, NEW_YORK), { text: "Due today", tone: "today" });
  // Late evening in New York is already the next day in UTC: still today.
  assert.deepEqual(dueLabel("2026-11-24T03:30:00Z", NOW, NEW_YORK), { text: "Due today", tone: "today" });
  assert.deepEqual(dueLabel("2026-11-24T15:00:00Z", NOW, NEW_YORK), { text: "Due tomorrow", tone: "upcoming" });
  assert.deepEqual(dueLabel("2026-11-26T15:00:00Z", NOW, NEW_YORK), { text: "Due Thu, Nov 26", tone: "upcoming" });
  assert.deepEqual(dueLabel("2026-11-22T15:00:00Z", NOW, NEW_YORK), { text: "Overdue 1 day · Sun, Nov 22", tone: "overdue" });
  assert.deepEqual(dueLabel("2026-11-20T15:00:00Z", NOW, NEW_YORK), { text: "Overdue 3 days · Fri, Nov 20", tone: "overdue" });
  // Another year says which.
  assert.deepEqual(dueLabel("2027-01-04T15:00:00Z", NOW, NEW_YORK), { text: "Due Mon, Jan 4, 2027", tone: "upcoming" });
});

test("who a task is assigned to", () => {
  assert.equal(assigneeText([]), "Unassigned");
  assert.equal(assigneeText(["Avery Example"]), "Avery Example");
  assert.equal(assigneeText(["Avery Example", "Blake Example"]), "Avery Example, Blake Example");
});

test("only a status that says more than open is shown", () => {
  assert.equal(statusText({ status: "to do", completed: false }), null);
  assert.equal(statusText({ status: "Open", completed: false }), null);
  assert.equal(statusText({ status: "", completed: false }), null);
  assert.equal(statusText({ status: "in progress", completed: false }), "In progress");
  assert.equal(statusText({ status: "waiting", completed: false }), "Waiting");
  // A task completed on the page says "Completed" itself.
  assert.equal(statusText({ status: "complete", completed: true }), null);
});

function task(source: TeamTask["source"], id: string): TeamTask {
  return { source, mine: true, list_id: null, id, title: id, description: "", due: null, assignees: [], status: "open", completed: false, url: null };
}

test("tasks are told apart across sources and counted by source", () => {
  assert.notEqual(taskKey(task("clickup", "1")), taskKey(task("quo", "1")));
  assert.equal(openCount([task("clickup", "a"), task("quo", "b"), { ...task("clickup", "c"), completed: true }]), 2);
  assert.equal(openCount([]), 0);
});

test("a status change takes only where the task stands from the API's answer", () => {
  const row: TeamTask = { ...task("quo", "TKone"), title: "Call the plumber back", assignees: ["Blake Example"] };
  // The answer's names can be missing when the name lookup failed after the change.
  const answer: TeamTask = { ...row, assignees: ["Someone"], status: "complete", completed: true };
  assert.deepEqual(withStatus(row, answer), { ...row, status: "complete", completed: true });
});
