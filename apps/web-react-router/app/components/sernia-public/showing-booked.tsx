import { useEffect, useId, useRef, useState } from "react";

import { CallLine } from "~/components/sernia-public/call-line";
import {
  type BookingResult,
  type BookingView,
  NETWORK_MESSAGE,
  PUBLIC_API,
  bookingPlace,
  formatSpan,
  readRefusal,
} from "~/lib/showing-booking-page";

/**
 * After booking: when and where, the number we texted with "Wrong number?
 * Fix it", and a pointer to the cancel link in the text. A fix texts the
 * confirmation to the new number, at most twice per booking (the API says
 * how many are left).
 */
export function ShowingBooked({ result, siteKey }: { result: BookingResult; siteKey: string | null }) {
  const [view, setView] = useState<BookingView>(result.booking);
  const [fixing, setFixing] = useState(false);
  const [mobile, setMobile] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [fieldLine, setFieldLine] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [noMore, setNoMore] = useState(view.corrections_left <= 0);
  const [busy, setBusy] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const id = useId();

  useEffect(() => {
    heading.current?.focus();
  }, []);

  useEffect(() => {
    if (fixing) input.current?.focus();
  }, [fixing]);

  async function fix(event: React.FormEvent) {
    event.preventDefault();
    if (busy) return;
    setProblem(null);
    setFieldLine(null);
    if (!mobile.trim()) {
      setFieldLine("Enter a ten-digit US mobile number.");
      input.current?.focus();
      return;
    }
    setBusy(true);
    try {
      let response: Response;
      try {
        response = await fetch(`${PUBLIC_API}/bookings/mobile`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ cancel_token: result.cancel_token, mobile }),
        });
      } catch {
        setProblem(NETWORK_MESSAGE);
        return;
      }
      const body = await response.json().catch(() => null);
      if (response.ok && body) {
        const next = body as BookingView;
        setView(next);
        setFixing(false);
        setMobile("");
        setNoMore(next.corrections_left <= 0);
        setDone(`Done. We texted the confirmation to ${next.mobile}.`);
        return;
      }
      const refusal = readRefusal(response.status, body);
      if (refusal.code === "corrections_used") {
        setFixing(false);
        setNoMore(true);
        return;
      }
      setProblem(refusal.message);
      setFieldLine(refusal.fields.mobile ?? null);
      input.current?.focus();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <h1 ref={heading} tabIndex={-1} className="text-2xl font-semibold tracking-tight outline-none sm:text-3xl">
        Your showing is booked
      </h1>

      <dl className="space-y-3 rounded-lg border p-4 text-[15px]">
        <div>
          <dt className="text-sm text-muted-foreground">When</dt>
          <dd className="font-medium">{formatSpan(view.starts_at, view.ends_at, view.timezone)}</dd>
        </div>
        <div>
          <dt className="text-sm text-muted-foreground">Where</dt>
          <dd className="font-medium">{bookingPlace(view)}</dd>
        </div>
      </dl>

      <div className="space-y-3">
        <p className="leading-7">
          We texted {view.mobile}.{" "}
          {!noMore && !fixing && (
            <>
              Wrong number?{" "}
              <button
                type="button"
                onClick={() => {
                  setDone(null);
                  setFixing(true);
                }}
                className="font-medium underline underline-offset-4"
              >
                Fix it
              </button>
            </>
          )}
        </p>

        {fixing && (
          <form noValidate onSubmit={fix} className="space-y-3 rounded-lg border p-4">
            <div className="space-y-1.5">
              <label htmlFor={`${id}-mobile`} className="block text-sm font-medium">
                Your mobile
              </label>
              <input
                id={`${id}-mobile`}
                ref={input}
                name="mobile"
                type="tel"
                inputMode="tel"
                autoComplete="tel-national"
                value={mobile}
                onChange={(event) => {
                  setMobile(event.target.value);
                  setFieldLine(null);
                }}
                aria-invalid={Boolean(fieldLine)}
                aria-describedby={fieldLine ? `${id}-mobile-error` : undefined}
                className="block w-full rounded-md border border-input bg-background px-3 py-2 text-base outline-none focus-visible:ring-2 focus-visible:ring-ring aria-[invalid=true]:border-destructive"
              />
              {fieldLine && (
                <p id={`${id}-mobile-error`} className="text-sm font-medium text-destructive">
                  {fieldLine}
                </p>
              )}
            </div>
            <div className="flex flex-wrap gap-3">
              <button
                type="submit"
                disabled={busy}
                className="rounded-lg bg-primary px-4 py-2.5 text-sm font-semibold text-primary-foreground hover:bg-primary/90 disabled:opacity-70"
              >
                {busy ? "Texting…" : "Text this number"}
              </button>
              <button
                type="button"
                onClick={() => {
                  setFixing(false);
                  setProblem(null);
                  setFieldLine(null);
                }}
                className="rounded-lg border px-4 py-2.5 text-sm font-medium hover:bg-muted"
              >
                Keep {view.mobile}
              </button>
            </div>
          </form>
        )}

        <p role="status" className="text-sm font-medium">
          {problem ? <span className="text-destructive">{problem}</span> : done}
        </p>

        {noMore && <CallLine siteKey={siteKey} lead="To change the number again," tail="." />}
      </div>

      <p className="leading-7 text-muted-foreground">The text has a link to cancel if your plans change.</p>
    </div>
  );
}
