// Tells the API which conversation this page has in front of its reader, so
// that pushes about it are not sent to them while they are reading it (see
// api/src/agent_conversations/push/presence.py). The page says so every
// PRESENCE_INTERVAL_MS while it is the one in use, and says it left as soon as
// it is hidden, loses focus on a computer, or closes. The API forgets a page
// that stops reporting after 40 seconds, so a lost "left" only delays pushes.

export const PRESENCE_INTERVAL_MS = 15_000;

/** What a page reports: the conversation in front of the reader, or null. */
export interface PresenceReport {
  client_id: string;
  conversation_id: string | null;
  seq: number;
}

type Listening = Pick<EventTarget, "addEventListener" | "removeEventListener">;

export interface PresenceDocument extends Listening {
  visibilityState: DocumentVisibilityState;
  hasFocus(): boolean;
}

export interface PresenceWindow extends Listening {
  matchMedia(query: string): { matches: boolean };
  setInterval(handler: () => void, timeout: number): number;
  clearInterval(id: number): void;
}

/**
 * The page is the one its reader is using. A phone or tablet shows one app at
 * a time, so on screen is in front; iOS can also report no focus until the
 * page is tapped. A computer can show a window beside the one in use, so
 * there it must have the focus too.
 */
export function inFront(doc: PresenceDocument, win: PresenceWindow): boolean {
  if (doc.visibilityState !== "visible") return false;
  if (win.matchMedia("(hover: none)").matches) return true;
  return doc.hasFocus();
}

let pageClientId: string | null = null;
let lastSeq = 0;

/** One ID for this page load, kept across conversations and remounts. */
function clientId(): string {
  pageClientId ??= crypto.randomUUID();
  return pageClientId;
}

/** The next report from this page. Numbered so the API can drop a late one. */
export function presenceReport(conversationId: string | null): PresenceReport {
  lastSeq += 1;
  return { client_id: clientId(), conversation_id: conversationId, seq: lastSeq };
}

/**
 * Report `conversationId` while the page is in front of its reader. `send`
 * gets each report as it is made; the returned function stops watching and
 * reports leaving if the page had said it was there.
 */
export function watchPresence(
  conversationId: string,
  send: (report: PresenceReport) => void,
  doc: PresenceDocument,
  win: PresenceWindow,
): () => void {
  let reported = false;

  // A heartbeat repeats "here"; an event reports only a change.
  const check = (heartbeat: boolean) => {
    const here = inFront(doc, win);
    if (here && (heartbeat || !reported)) send(presenceReport(conversationId));
    else if (!here && reported) send(presenceReport(null));
    reported = here;
  };
  const onChange = () => check(false);
  const onLeave = () => {
    if (reported) send(presenceReport(null));
    reported = false;
  };

  check(false);
  const timer = win.setInterval(() => check(true), PRESENCE_INTERVAL_MS);
  doc.addEventListener("visibilitychange", onChange);
  win.addEventListener("focus", onChange);
  win.addEventListener("blur", onChange);
  win.addEventListener("pageshow", onChange);
  win.addEventListener("pagehide", onLeave);

  return () => {
    win.clearInterval(timer);
    doc.removeEventListener("visibilitychange", onChange);
    win.removeEventListener("focus", onChange);
    win.removeEventListener("blur", onChange);
    win.removeEventListener("pageshow", onChange);
    win.removeEventListener("pagehide", onLeave);
    onLeave();
  };
}
