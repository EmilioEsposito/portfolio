import type { ChatRunStatus } from "./sernia-chat-recovery";

export interface ConversationSummary {
  conversation_id: string;
  modality: string;
  preview: string;
  has_pending: boolean;
  trigger_source: string | null;
  trigger_contact_name: string | null;
  participant?: string;
  created_at: string | null;
  updated_at: string | null;
  run?: ChatRunStatus | null;
}

export const CONVERSATIONS_CHANGED = "sernia-conversations-changed";
const pendingConversations = new Map<string, ConversationSummary>();

export function refreshConversations() {
  window.dispatchEvent(new Event(CONVERSATIONS_CHANGED));
}

export function conversationStarted(
  id: string,
  requestId: string,
  preview: string,
) {
  const now = new Date().toISOString();
  pendingConversations.set(id, {
    conversation_id: id,
    modality: "web_chat",
    preview: preview.slice(0, 100) || "Attachment",
    has_pending: false,
    trigger_source: null,
    trigger_contact_name: null,
    created_at: now,
    updated_at: now,
    run: { id: requestId, status: "running", started_at: now },
  });
  refreshConversations();
}

export function conversationSettled(id: string, requestId?: string) {
  if (pendingConversations.get(id)?.run?.id === requestId)
    pendingConversations.delete(id);
  refreshConversations();
}

export function mergeConversationSummaries(
  server: ConversationSummary[],
  pending: Iterable<ConversationSummary> = pendingConversations.values(),
): ConversationSummary[] {
  const merged = new Map(
    server.map((conversation) => [conversation.conversation_id, conversation]),
  );
  for (const conversation of pending) {
    const saved = merged.get(conversation.conversation_id);
    if (saved?.run?.id === conversation.run?.id) continue;
    merged.set(conversation.conversation_id, {
      ...conversation,
      ...(saved
        ? { preview: saved.preview, created_at: saved.created_at }
        : {}),
    });
  }
  return [...merged.values()].sort((a, b) =>
    (b.updated_at || "").localeCompare(a.updated_at || ""),
  );
}

export function acceptConversationSummaries(server: ConversationSummary[]) {
  for (const conversation of server) {
    if (
      pendingConversations.get(conversation.conversation_id)?.run?.id ===
      conversation.run?.id
    ) {
      pendingConversations.delete(conversation.conversation_id);
    }
  }
  return mergeConversationSummaries(server);
}
