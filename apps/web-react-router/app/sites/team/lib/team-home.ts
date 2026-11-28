import { leadThreadPath } from "~/sites/team/lib/lead-inbox";
import type { ConversationSummary } from "~/sites/team/lib/sernia-conversations";
import { dueLabel, type TasksView, type TaskSource } from "~/sites/team/lib/team-tasks";

// What the team home and the navigation count: the approvals waiting on a
// person (GET /api/sernia-ai/workflow/pending) and the open tasks
// (GET /api/team-tasks). No React here.

/** How many conversations with an approval waiting the team home lists. */
export const APPROVALS_SHOWN = 5;

export type Approvals = {
  /** Conversations with a step waiting for someone to approve or reject it. */
  count: number;
  /** The most recently updated of them, at most APPROVALS_SHOWN. */
  first: ConversationSummary[];
  /** Every one of them, for the chat's conversation list to mark. */
  ids: string[];
};

/**
 * The conversations the pending list returned, most recent first. The list
 * has only conversations with an approval waiting; `has_pending` is checked
 * again in case an older API answers with others.
 */
export function approvalsFrom(conversations: ConversationSummary[], shown = APPROVALS_SHOWN): Approvals {
  const waiting = conversations
    .filter((conversation) => conversation.has_pending !== false)
    .sort((a, b) => stamp(b) - stamp(a));
  return {
    count: waiting.length,
    first: waiting.slice(0, shown),
    ids: waiting.map((conversation) => conversation.conversation_id),
  };
}

function stamp(conversation: ConversationSummary): number {
  const time = Date.parse(conversation.updated_at ?? conversation.created_at ?? "");
  return Number.isNaN(time) ? 0 : time;
}

export type TaskCounts = {
  /** Tasks not yet completed. */
  open: number;
  /** Open tasks whose date has passed, by calendar day in the reader's time zone. */
  overdue: number;
  /** Sources that could not be read, or were cut short: the counts may be low. */
  partial: TaskSource[];
};

export function taskCounts(view: Pick<TasksView, "tasks" | "unavailable" | "incomplete">, now: Date, timeZone?: string): TaskCounts {
  const open = view.tasks.filter((task) => task.mine && !task.completed);
  return {
    open: open.length,
    overdue: open.filter((task) => dueLabel(task.due, now, timeZone).tone === "overdue").length,
    partial: [...new Set([...view.unavailable, ...view.incomplete])],
  };
}

/** "2 to approve" */
export function approvalsText(count: number): string {
  return count === 0 ? "Nothing to approve" : `${count} to approve`;
}

/** "14 open" or "14+ open" where a source was not read whole. */
export function openText(counts: TaskCounts): string {
  if (counts.open === 0) return counts.partial.length ? "Tasks not read" : "No tasks assigned to you";
  return `${counts.open}${counts.partial.length ? "+" : ""} assigned to you`;
}

/** "14 open, 3 overdue" */
export function tasksText(counts: TaskCounts): string {
  return counts.overdue ? `${openText(counts)}, ${counts.overdue} overdue` : openText(counts);
}

/** Where to decide one conversation: a Zillow lead's page, or the chat. */
export function conversationHref(conversationId: string, leadThreadId?: string | null): string {
  if (leadThreadId) return leadThreadPath(leadThreadId);
  return `/team/chat?id=${encodeURIComponent(conversationId)}`;
}

/** Who a conversation is with, when it says. */
export function conversationWith(conversation: ConversationSummary): string | null {
  return conversation.trigger_contact_name || conversation.participant || null;
}
