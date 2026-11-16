import { useEffect, useRef, useState } from "react";
import { Link, useRevalidator } from "react-router";

import { type BookingView, PUBLIC_API, bookingPlace, formatSpan } from "~/lib/showing-booking-page";
import { type CancelState, cancelCopy, cancelState, readCancelAnswer } from "~/lib/showing-cancel-page";

/**
 * A booking and its Cancel button, then what happened. The heading takes
 * focus when the state changes, so a screen reader hears the outcome.
 */
export function ShowingCancel({ token, initial }: { token: string; initial: BookingView }) {
  const [view, setView] = useState<BookingView>(initial);
  const [state, setState] = useState<CancelState>(cancelState(initial));
  const [gone, setGone] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const changed = useRef(false);
  // Read the booking again after a change, so the page's title follows it.
  const revalidator = useRevalidator();

  useEffect(() => {
    if (changed.current) heading.current?.focus();
  }, [state, gone]);

  async function cancel() {
    if (busy) return;
    setBusy(true);
    setProblem(null);
    let status: number | null = null;
    let body: unknown = null;
    try {
      const response = await fetch(`${PUBLIC_API}/bookings/cancel`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ cancel_token: token }),
        referrerPolicy: "no-referrer",
      });
      status = response.status;
      body = await response.json().catch(() => null);
    } catch {
      status = null;
    }
    const outcome = readCancelAnswer(status, body);
    changed.current = true;
    if (outcome.kind === "view") {
      setView(outcome.view);
      setState(cancelState(outcome.view, true));
      void revalidator.revalidate();
    } else if (outcome.kind === "started") {
      setState("started");
    } else if (outcome.kind === "unknown") {
      setGone(outcome.message);
    } else {
      setProblem(outcome.message);
    }
    setBusy(false);
  }

  if (gone) {
    return (
      <div className="space-y-3">
        <h1 ref={heading} tabIndex={-1} className="text-2xl font-semibold tracking-tight outline-none">
          We couldn't find this booking
        </h1>
        <p className="leading-7 text-muted-foreground">{gone}</p>
      </div>
    );
  }

  const copy = cancelCopy(state, view.unit);
  const cancelled = state === "cancelled_now" || state === "already_cancelled" || state === "cancelled_by_team";
  return (
    <div className="space-y-6">
      <h1 ref={heading} tabIndex={-1} className="text-2xl font-semibold tracking-tight outline-none sm:text-3xl">
        {copy.heading}
      </h1>

      <dl className="space-y-3 rounded-lg border p-4 text-[15px]">
        <div>
          <dt className="text-sm text-muted-foreground">When</dt>
          <dd className={cancelled ? "font-medium text-muted-foreground line-through" : "font-medium"}>
            {formatSpan(view.starts_at, view.ends_at, view.timezone)}
          </dd>
        </div>
        <div>
          <dt className="text-sm text-muted-foreground">Where</dt>
          <dd className="font-medium">{bookingPlace(view)}</dd>
        </div>
      </dl>

      {copy.line && <p className="leading-7">{copy.line}</p>}

      {state === "booked" && (
        <div className="space-y-3">
          <button
            type="button"
            onClick={() => void cancel()}
            disabled={busy}
            aria-describedby={problem ? "cancel-problem" : undefined}
            className="w-full rounded-lg bg-red-700 px-4 py-3 text-base font-semibold text-white hover:bg-red-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:opacity-70 dark:bg-red-600 dark:hover:bg-red-700 sm:w-auto"
          >
            {busy ? "Cancelling…" : "Cancel showing"}
          </button>
          <p id="cancel-problem" role="status" className="text-sm font-medium text-red-700 dark:text-red-400">
            {problem}
          </p>
        </div>
      )}

      {copy.next && (
        <p>
          <Link to={copy.next.href} className="font-medium underline underline-offset-4">
            {copy.next.label}
          </Link>
        </p>
      )}
    </div>
  );
}
