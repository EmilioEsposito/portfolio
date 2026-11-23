/** Private chats load history from the server; never resend old attachment bytes. */
export function privateChatInput<T>(messages: T[]): T[] {
  return messages.slice(-1);
}
