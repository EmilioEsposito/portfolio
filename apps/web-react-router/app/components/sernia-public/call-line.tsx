import { useState } from "react";

import { useTurnstile } from "~/components/sernia-public/turnstile";
import {
  BOT_CHECK_RETRY,
  NETWORK_MESSAGE,
  PUBLIC_API,
  readRefusal,
  type TeamPhone,
} from "~/lib/showing-booking-page";

/**
 * "…Call us instead." with "Show phone number", which fetches the team's
 * number after a Turnstile check, so the number is never in the page source.
 * Once fetched, the line reads "Call 412-555-0100 (extension 1) instead",
 * the number a link that dials the extension.
 */
export function CallLine({
  siteKey,
  lead,
  tail = " instead.",
}: {
  siteKey: string | null;
  /** The sentence's start. "Call" starts a new sentence after "?" or ".", else "call" continues it. */
  lead?: string;
  /** What follows the number: " instead." by default. */
  tail?: string;
}) {
  const call = !lead || /[?.!]$/.test(lead) ? "Call" : "call";
  const turnstile = useTurnstile(siteKey);
  const [phone, setPhone] = useState<TeamPhone | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  async function reveal() {
    setBusy(true);
    setProblem(null);
    try {
      let token: string;
      try {
        token = await turnstile.token();
      } catch {
        setProblem(BOT_CHECK_RETRY);
        return;
      }
      let response: Response;
      try {
        response = await fetch(`${PUBLIC_API}/team-phone`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ turnstile_token: token }),
        });
      } catch {
        setProblem(NETWORK_MESSAGE);
        return;
      }
      const body = await response.json().catch(() => null);
      if (response.ok && body) setPhone(body as TeamPhone);
      else setProblem(readRefusal(response.status, body).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-2">
      <p className="text-sm leading-6 text-muted-foreground">
        {lead ? `${lead} ` : null}
        {phone ? (
          <>
            {call}{" "}
            <a href={phone.tel_href} className="font-medium text-foreground underline underline-offset-4">
              {phone.display}
            </a>{" "}
            (extension {phone.extension}){tail}
          </>
        ) : (
          <>
            {call} us{tail}{" "}
            <button
              type="button"
              onClick={reveal}
              disabled={busy}
              className="font-medium text-foreground underline underline-offset-4 disabled:opacity-60"
            >
              {busy ? "Getting the number…" : "Show phone number"}
            </button>
          </>
        )}
      </p>
      {problem && (
        <p role="alert" className="text-sm font-medium text-red-700 dark:text-red-400">
          {problem}
        </p>
      )}
      <div ref={turnstile.container} />
    </div>
  );
}
