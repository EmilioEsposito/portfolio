/**
 * A Zillow lead conversation handed to Sernia AI.
 *
 * The lead agent's conversations cannot be continued in chat. A message typed
 * in one starts a new chat with Sernia AI instead: its first message is what
 * the person typed, followed by a block of lead context written by the API
 * (`api/src/sernia_ai/lead_hand_off.py`). The page shows the block folded
 * away, so the bubble reads as the person's own words.
 */

// The first and last lines of the block. Keep in step with START and END in
// api/src/sernia_ai/lead_hand_off.py.
export const HAND_OFF_START = "[Hand-off from the Zillow lead agent]";
export const HAND_OFF_END = "[End of hand-off]";

/** The card that asks for a person. Nothing on it can be approved. */
export const LEAD_NEEDS_A_PERSON = "lead_needs_a_person";

/** The first message of a handed-off chat: the person's words, then the block. */
export function withHandOff(block: string, words: string): string {
  return `${words.trim()}\n\n${block.trim()}`;
}

/**
 * A user message split into the person's own words and the lead context.
 * `context` is null for every message that does not end with the block.
 */
export function splitHandOff(text: string): {
  context: string | null;
  words: string;
} {
  const opening = `\n${HAND_OFF_START}\n`;
  const closing = `\n${HAND_OFF_END}`;
  const start = text.indexOf(opening);
  const whole = text.trimEnd();
  if (start < 0 || !whole.endsWith(closing)) {
    return { context: null, words: text };
  }
  return {
    context: whole
      .slice(start + opening.length, whole.length - closing.length)
      .trim(),
    words: text.slice(0, start).trim(),
  };
}

/**
 * A hand-off started on a lead's page (/team/leads/threads/:threadId). That
 * page fetches the block, then opens /team/chat with no conversation id and
 * this in the router state; the chat page starts a new chat with it, and its
 * first navigation (to the new chat's id) drops the state, so a reload or Back
 * does not send it again.
 */
export interface ArrivingHandOff {
  /** The lead conversation the hand-off closes. */
  from: string;
  /** The first message: the person's words, then the block (`withHandOff`). */
  text: string;
}

export function handOffFromState(state: unknown): ArrivingHandOff | null {
  const handOff = (state as { handOff?: { from?: unknown; text?: unknown } } | null)?.handOff;
  if (!handOff || typeof handOff.from !== "string" || typeof handOff.text !== "string") return null;
  if (!handOff.from || !handOff.text.includes(HAND_OFF_START)) return null;
  return { from: handOff.from, text: handOff.text };
}
