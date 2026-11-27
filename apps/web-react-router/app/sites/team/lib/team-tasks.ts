/**
 * The team's Tasks page: open ClickUp and Quo tasks as the API answers them
 * (api/src/team_tasks), and how a task's date and status read. No React here.
 */

export type TaskSource = "clickup" | "quo";

export type ClickUpList = { id: string; name: string; state: "ready" | "unavailable" | "incomplete" };
export type TaskDraft = { list_id: string; title: string; description: string };

export type TeamTask = {
  mine: boolean;
  list_id: string | null;
  source: TaskSource;
  id: string;
  title: string;
  description: string;
  /** ISO time, or null for a task with no date. ClickUp calls it the reminder date. */
  due: string | null;
  assignees: string[];
  /** The source's own word for where the task stands: "to do", "waiting", "open". */
  status: string;
  completed: boolean;
  /** The task in its own app. Quo has no web address for a task. */
  url: string | null;
};

export type TasksView = {
  tasks: TeamTask[];
  clickup_lists: ClickUpList[];
  /** Sources that could not be read just now; their tasks are missing. */
  unavailable: TaskSource[];
  /** Sources with more tasks than the API reads; some of their open tasks may be missing. */
  incomplete: TaskSource[];
};

/**
 * A row after its status was changed. Only where the task stands is taken
 * from the answer: nothing else about it changed, and the answer's other
 * fields are filled in separately (a name lookup can fail on its own).
 */
export function withStatus(task: TeamTask, answer: Pick<TeamTask, "completed" | "status">): TeamTask {
  return { ...task, completed: answer.completed, status: answer.status };
}

export const SOURCE_NAME: Record<TaskSource, string> = { clickup: "ClickUp", quo: "Quo" };

/** One task among both sources: ids are only unique within a source. */
export function taskKey(task: Pick<TeamTask, "source" | "id">): string {
  return `${task.source}:${task.id}`;
}

/** The calendar day of a moment in a time zone, as days since 1970. */
function dayNumber(moment: Date, timeZone: string | undefined): number {
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone, year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(moment);
  const part = (type: string) => Number(parts.find((entry) => entry.type === type)?.value);
  return Math.round(Date.UTC(part("year"), part("month") - 1, part("day")) / 86_400_000);
}

export type DueTone = "overdue" | "today" | "upcoming" | "none";

/**
 * How a due date reads on the page, by calendar day in the reader's time zone:
 * "Overdue 3 days · Fri, Nov 20", "Due today", "Due tomorrow", "Due Thu, Nov 26".
 */
export function dueLabel(due: string | null, now: Date, timeZone?: string): { text: string; tone: DueTone } {
  if (!due) return { text: "No due date", tone: "none" };
  const date = new Date(due);
  if (Number.isNaN(date.getTime())) return { text: "No due date", tone: "none" };
  const days = dayNumber(date, timeZone) - dayNumber(now, timeZone);
  const sameYear =
    new Intl.DateTimeFormat("en-US", { timeZone, year: "numeric" }).format(date) ===
    new Intl.DateTimeFormat("en-US", { timeZone, year: "numeric" }).format(now);
  const day = new Intl.DateTimeFormat("en-US", {
    timeZone,
    weekday: "short",
    month: "short",
    day: "numeric",
    ...(sameYear ? {} : { year: "numeric" }),
  }).format(date);
  if (days < 0) return { text: `Overdue ${-days} ${days === -1 ? "day" : "days"} · ${day}`, tone: "overdue" };
  if (days === 0) return { text: "Due today", tone: "today" };
  if (days === 1) return { text: "Due tomorrow", tone: "upcoming" };
  return { text: `Due ${day}`, tone: "upcoming" };
}

/** "Unassigned", one name, or the names in a row. */
export function assigneeText(assignees: string[]): string {
  return assignees.length ? assignees.join(", ") : "Unassigned";
}

/**
 * The status worth showing: a ClickUp task's own column ("In progress",
 * "Waiting"). A plain open task says nothing, since every task listed is open.
 */
export function statusText(task: Pick<TeamTask, "status" | "completed">): string | null {
  const status = task.status.trim();
  if (task.completed || !status || ["open", "to do"].includes(status.toLowerCase())) return null;
  return status.charAt(0).toUpperCase() + status.slice(1);
}

/** Completed rows remain visible for Undo, but leave the open counts immediately. */
export function openCount(tasks: TeamTask[]): number {
  return tasks.filter((task) => !task.completed).length;
}
