import type { UIMessage } from "ai";

/** Keep tool evidence in the UI, but send only bounded text as public context. */
export function publicChatHistory(messages: UIMessage[]) {
  const history = [];
  let remaining = 12_000;
  let remainingBytes = 20_000;
  const encoder = new TextEncoder();
  for (const message of messages.slice(-16).reverse()) {
    if (message.role !== "user" && message.role !== "assistant") continue;
    const text = message.parts
      .filter((part) => part.type === "text")
      .map((part) => part.text)
      .join("\n")
      .slice(0, 4_000);
    if (!text.trim()) continue;
    const entry = { id: message.id, role: message.role, parts: [{ type: "text" as const, text }] };
    const bytes = encoder.encode(JSON.stringify(entry)).length;
    if (text.length > remaining || bytes > remainingBytes) break;
    remaining -= text.length;
    remainingBytes -= bytes;
    history.unshift(entry);
  }
  return history;
}
