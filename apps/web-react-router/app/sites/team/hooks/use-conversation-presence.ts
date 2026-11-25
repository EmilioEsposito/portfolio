import { useEffect, useRef } from "react";
import {
  watchPresence,
  type PresenceReport,
} from "~/sites/team/lib/conversation-presence";

const PRESENCE_URL = "/api/sernia-ai/push/presence";

/**
 * While this conversation is in front of its reader, the API sends them no
 * pushes about it. See lib/conversation-presence.ts.
 */
export function useConversationPresence(
  conversationId: string,
  getToken: () => Promise<string | null>,
): void {
  // Clerk may hand a new getToken on a render; that is no reason to report
  // leaving and coming back.
  const tokenRef = useRef(getToken);
  tokenRef.current = getToken;

  useEffect(() => {
    const send = (report: PresenceReport) => {
      void (async () => {
        const token = await tokenRef.current();
        if (!token) return;
        await fetch(PRESENCE_URL, {
          method: "POST",
          // A "left" sent as the page is hidden or closed still goes out.
          keepalive: true,
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify(report),
        });
      })().catch(() => {
        // Not reporting only means the reader may get a push they did not need.
      });
    };
    return watchPresence(conversationId, send, document, window);
  }, [conversationId]);
}
