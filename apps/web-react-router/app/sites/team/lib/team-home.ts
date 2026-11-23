import type { ConversationSummary } from "~/sites/team/lib/sernia-conversations";

// What the team home shows under "Waiting for a person", from the list the chat's
// conversation sidebar loads (GET /api/sernia-ai/conversations/history).

/** How many recent conversations the team home reads, as the sidebar does. */
export const RECENT_CONVERSATIONS = 50;
/** How many waiting conversations it lists. */
export const WAITING_SHOWN = 5;

export type Waiting = {
  /** Conversations with something waiting for a person, among those read. */
  count: number;
  /** The most recently updated of them, at most WAITING_SHOWN. */
  first: ConversationSummary[];
};

export function waitingForPerson(conversations: ConversationSummary[], shown = WAITING_SHOWN): Waiting {
  const waiting = conversations
    .filter((conversation) => conversation.has_pending)
    .sort((a, b) => stamp(b) - stamp(a));
  return { count: waiting.length, first: waiting.slice(0, shown) };
}

function stamp(conversation: ConversationSummary): number {
  const time = Date.parse(conversation.updated_at ?? conversation.created_at ?? "");
  return Number.isNaN(time) ? 0 : time;
}

/** The chat page for one conversation. */
export function conversationHref(conversationId: string): string {
  return `/team/chat?id=${encodeURIComponent(conversationId)}`;
}

/** Who a conversation is with, when it says. */
export function conversationWith(conversation: ConversationSummary): string | null {
  return conversation.trigger_contact_name || conversation.participant || null;
}
