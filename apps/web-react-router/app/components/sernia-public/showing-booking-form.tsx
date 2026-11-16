import { useEffect, useId, useMemo, useRef, useState } from "react";

import { CallLine } from "~/components/sernia-public/call-line";
import { useTurnstile } from "~/components/sernia-public/turnstile";
import {
  BOT_CHECK_MESSAGE,
  type BookingResult,
  EMPTY_FORM,
  type FormValues,
  NETWORK_MESSAGE,
  PUBLIC_API,
  type UnitPage,
  bookingBody,
  fieldOrder,
  findOffer,
  firstMarked,
  formatDay,
  formatTime,
  groupOffers,
  missingFields,
  readRefusal,
  refusalAction,
  refusalMessage,
  tickField,
  ticksMessage,
  timeNote,
} from "~/lib/showing-booking-page";
import { cn } from "~/lib/utils";

const INPUT =
  "block w-full rounded-md border border-input bg-background px-3 py-2 text-base outline-none focus-visible:ring-2 focus-visible:ring-ring aria-[invalid=true]:border-destructive";
const ERROR_LINE = "text-sm font-medium text-destructive";

type Status = { tone: "error" | "info"; text: string } | null;

async function fetchUnit(unit: string): Promise<UnitPage | null> {
  const response = await fetch(`${PUBLIC_API}/units/${encodeURIComponent(unit)}`, {
    headers: { accept: "application/json" },
  });
  return response.ok ? ((await response.json()) as UnitPage) : null;
}

/**
 * The booking form: a time, name, mobile and email, the ticks the API asks
 * for, the call line and the Book button. A plain form with real labels, so a
 * person's own AI agent can read and fill it as a person does. Whatever
 * happens, what the person typed stays in the form.
 */
export function ShowingBookingForm({
  initialUnit,
  leadToken,
  onBooked,
}: {
  initialUnit: UnitPage;
  leadToken: string | null;
  onBooked: (result: BookingResult) => void;
}) {
  const [unit, setUnit] = useState(initialUnit);
  const [values, setValues] = useState<FormValues>(EMPTY_FORM);
  const [fields, setFields] = useState<Record<string, string>>({});
  const [status, setStatus] = useState<Status>(null);
  const [busy, setBusy] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(true);
  const [focusTarget, setFocusTarget] = useState<{ name: string } | null>(null);
  const refs = useRef<Record<string, HTMLElement | null>>({});
  const bookButton = useRef<HTMLButtonElement>(null);
  const turnstile = useTurnstile(unit.turnstile_site_key);
  const id = useId();

  const order = useMemo(() => fieldOrder(unit.confirmations), [unit.confirmations]);

  useEffect(() => {
    if (!focusTarget) return;
    const element = refs.current[focusTarget.name] ?? bookButton.current;
    element?.focus();
    element?.scrollIntoView({ block: "center" });
  }, [focusTarget]);

  function update(patch: Partial<FormValues>, field: string) {
    setValues((current) => ({ ...current, ...patch }));
    setFields((current) => {
      if (!current[field]) return current;
      const { [field]: _cleared, ...rest } = current;
      return rest;
    });
  }

  function mark(next: Record<string, string>, message: string) {
    setFields(next);
    setStatus({ tone: "error", text: message });
    const first = firstMarked(next, order);
    if (first === "start") setPickerOpen(true);
    setFocusTarget({ name: first && first !== "turnstile" ? first : "book" });
  }

  async function reloadUnit(): Promise<UnitPage | null> {
    try {
      const fresh = await fetchUnit(unit.unit);
      if (fresh) setUnit(fresh);
      return fresh;
    } catch {
      return null;
    }
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (busy) return;
    const missing = missingFields(values, unit.confirmations);
    if (Object.keys(missing).length > 0) {
      const onlyTicks = Object.keys(missing).every((name) => name.startsWith("confirmations."));
      mark(missing, (onlyTicks && ticksMessage(missing)) || "Check the marked fields above.");
      return;
    }
    setBusy(true);
    setStatus(null);
    setFields({});
    try {
      let token: string;
      try {
        token = await turnstile.token();
      } catch {
        mark({ turnstile: BOT_CHECK_MESSAGE }, BOT_CHECK_MESSAGE);
        return;
      }
      let response: Response;
      try {
        response = await fetch(`${PUBLIC_API}/units/${encodeURIComponent(unit.unit)}/bookings`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(bookingBody(values, unit, token, leadToken)),
        });
      } catch {
        setStatus({ tone: "error", text: NETWORK_MESSAGE });
        setFocusTarget({ name: "book" });
        return;
      }
      const body = await response.json().catch(() => null);
      if (response.status === 201 && body) {
        onBooked(body as BookingResult);
        return;
      }
      const refusal = readRefusal(response.status, body);
      const message = refusalMessage(refusal);
      switch (refusalAction(refusal)) {
        case "pick_again": {
          const fresh = await reloadUnit();
          setValues((current) => ({ ...current, start: null }));
          const none = fresh !== null && fresh.offers.length === 0;
          mark({ start: refusal.fields.start ?? message }, none ? `${message} No other times are open online right now.` : message);
          return;
        }
        case "tick_again": {
          const fresh = await reloadUnit();
          setValues((current) => ({ ...current, ticks: {} }));
          const ticks = fresh?.confirmations ?? unit.confirmations;
          mark(
            Object.fromEntries(ticks.map((item) => [tickField(item.key), "Read this box again and tick it."])),
            message,
          );
          return;
        }
        case "call": {
          const text = /call us/i.test(message) ? message : `${message} You can call us instead.`;
          setFields(refusal.fields);
          setStatus({ tone: "error", text });
          setFocusTarget({ name: "book" });
          return;
        }
        default:
          mark(refusal.fields, message);
      }
    } finally {
      setBusy(false);
    }
  }

  const describedBy = (name: string, hint?: string) =>
    [hint, fields[name] ? `${id}-${name}-error` : null].filter(Boolean).join(" ") || undefined;
  const errorLine = (name: string) =>
    fields[name] ? (
      <p id={`${id}-${name}-error`} className={ERROR_LINE}>
        {fields[name]}
      </p>
    ) : null;

  if (unit.offers.length === 0) {
    return (
      <div className="space-y-4">
        <p className="leading-7">No times are open online right now. We can still find a time with you by phone.</p>
        <CallLine siteKey={unit.turnstile_site_key} />
      </div>
    );
  }

  const texts = unit.confirmations.find((item) => item.key === "texts");

  return (
    <form noValidate onSubmit={submit} className="space-y-8" aria-describedby={`${id}-status`}>
      <TimePicker
        unit={unit}
        value={values.start}
        open={pickerOpen}
        onOpen={() => {
          setPickerOpen(true);
          setFocusTarget({ name: "start" });
        }}
        onPick={(start) => {
          update({ start }, "start");
          setPickerOpen(false);
          setFocusTarget({ name: "start" });
        }}
        error={fields.start}
        errorId={`${id}-start-error`}
        focusRef={(element) => {
          refs.current.start = element;
        }}
      />

      <fieldset className="space-y-4">
        <legend className="mb-3 text-base font-semibold">Your details</legend>
        <div className="space-y-1.5">
          <label htmlFor={`${id}-name`} className="block text-sm font-medium">
            Name
          </label>
          <input
            id={`${id}-name`}
            ref={(element) => {
              refs.current.name = element;
            }}
            name="name"
            autoComplete="name"
            value={values.name}
            onChange={(event) => update({ name: event.target.value }, "name")}
            aria-invalid={Boolean(fields.name)}
            aria-describedby={describedBy("name")}
            className={INPUT}
          />
          {errorLine("name")}
        </div>
        <div className="space-y-1.5">
          <label htmlFor={`${id}-mobile`} className="block text-sm font-medium">
            Mobile
          </label>
          <input
            id={`${id}-mobile`}
            ref={(element) => {
              refs.current.mobile = element;
            }}
            name="mobile"
            type="tel"
            inputMode="tel"
            autoComplete="tel-national"
            value={values.mobile}
            onChange={(event) => update({ mobile: event.target.value }, "mobile")}
            aria-invalid={Boolean(fields.mobile)}
            aria-describedby={describedBy("mobile", `${id}-mobile-hint`)}
            className={INPUT}
          />
          <p id={`${id}-mobile-hint`} className="text-sm text-muted-foreground">
            A US mobile number. We text your confirmation to it.
          </p>
          {errorLine("mobile")}
        </div>
        <div className="space-y-1.5">
          <label htmlFor={`${id}-email`} className="block text-sm font-medium">
            Email
          </label>
          <input
            id={`${id}-email`}
            ref={(element) => {
              refs.current.email = element;
            }}
            name="email"
            type="email"
            autoComplete="email"
            value={values.email}
            onChange={(event) => update({ email: event.target.value }, "email")}
            aria-invalid={Boolean(fields.email)}
            aria-describedby={describedBy("email")}
            className={INPUT}
          />
          {errorLine("email")}
        </div>
      </fieldset>

      <fieldset className="space-y-3">
        <legend className="mb-3 text-base font-semibold">To book online, tick each box</legend>
        {unit.confirmations.map((item) => {
          const name = tickField(item.key);
          const checked = Boolean(values.ticks[item.key]);
          return (
            <div key={item.key} className="space-y-1.5">
              <label
                className={cn(
                  "flex cursor-pointer items-start gap-3 rounded-lg border px-3 py-3 text-[15px] leading-6",
                  checked && "border-primary/60 bg-primary/5",
                  fields[name] && "border-destructive bg-destructive/5",
                )}
              >
                <input
                  type="checkbox"
                  name={name}
                  ref={(element) => {
                    refs.current[name] = element;
                  }}
                  checked={checked}
                  onChange={(event) =>
                    update({ ticks: { ...values.ticks, [item.key]: event.target.checked } }, name)
                  }
                  aria-invalid={Boolean(fields[name])}
                  aria-describedby={describedBy(name, item.key === "texts" ? `${id}-small-print` : undefined)}
                  className="mt-1 size-5 shrink-0 accent-primary"
                />
                <span className="font-medium">{item.wording}</span>
              </label>
              {errorLine(name)}
            </div>
          );
        })}
        {texts && (
          <p id={`${id}-small-print`} className="text-sm leading-6 text-muted-foreground">
            {unit.consent_small_print}{" "}
            <a href={unit.privacy_path} target="_blank" rel="noopener" className="underline underline-offset-4">
              Privacy Policy
            </a>{" "}
            ·{" "}
            <a href={unit.sms_terms_path} target="_blank" rel="noopener" className="underline underline-offset-4">
              SMS Terms
            </a>
          </p>
        )}
        {errorLine("confirmations")}
        <CallLine siteKey={unit.turnstile_site_key} lead="Something doesn't fit, or prefer not to get texts?" />
      </fieldset>

      <div className="space-y-3">
        <div ref={turnstile.container} />
        <button
          ref={(element) => {
            bookButton.current = element;
            refs.current.book = element;
          }}
          type="submit"
          disabled={busy}
          className="w-full rounded-lg bg-primary px-4 py-3.5 text-base font-semibold text-primary-foreground transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:opacity-70"
        >
          {busy ? "Booking…" : "Book in-person showing"}
        </button>
        <p
          id={`${id}-status`}
          role="status"
          className={cn("min-h-6 text-sm leading-6", status?.tone === "error" && "font-medium text-destructive")}
        >
          {status?.text}
        </p>
      </div>
    </form>
  );
}

/**
 * Pick a day, then a time on it, in the unit's time zone. Once a time is
 * picked the picker folds to one row with Change, as in the mockup.
 */
function TimePicker({
  unit,
  value,
  open,
  onOpen,
  onPick,
  error,
  errorId,
  focusRef,
}: {
  unit: UnitPage;
  value: string | null;
  open: boolean;
  onOpen: () => void;
  onPick: (start: string) => void;
  error: string | undefined;
  errorId: string;
  focusRef: (element: HTMLElement | null) => void;
}) {
  const days = useMemo(() => groupOffers(unit.offers, unit.timezone), [unit.offers, unit.timezone]);
  const picked = findOffer(unit.offers, value);
  const [dayKey, setDayKey] = useState<string | null>(null);
  const day = days.find((item) => item.key === dayKey) ?? days.find((item) => item.times.some((t) => t.start === value)) ?? days[0];
  const headingId = useId();

  const errorLine = error ? (
    <p id={errorId} className={ERROR_LINE}>
      {error}
    </p>
  ) : null;

  if (!open && picked) {
    const note = timeNote(picked);
    return (
      <section aria-labelledby={headingId} className="space-y-3">
        <h2 id={headingId} className="text-base font-semibold">
          Time
        </h2>
        <div className="flex items-center justify-between gap-3 rounded-lg border px-3 py-3 text-[15px]">
          <span>
            <b className="font-semibold">{formatDay(picked.start, unit.timezone)}</b> at{" "}
            {formatTime(picked.start, unit.timezone)}
            {note && <span className="text-muted-foreground"> · {note}</span>}
          </span>
          <button
            type="button"
            ref={focusRef}
            onClick={onOpen}
            aria-label={`Change time, ${formatDay(picked.start, unit.timezone)} at ${formatTime(picked.start, unit.timezone)} picked`}
            className="shrink-0 font-medium underline underline-offset-4"
          >
            Change
          </button>
        </div>
        {errorLine}
      </section>
    );
  }

  return (
    <section aria-labelledby={headingId} className="space-y-3">
      <h2 id={headingId} className="text-base font-semibold">
        Pick a time
      </h2>
      <div role="group" aria-label="Day" className="flex flex-wrap gap-2">
        {days.map((item, index) => {
          const current = item.key === day?.key;
          return (
            <button
              key={item.key}
              type="button"
              ref={index === 0 ? focusRef : undefined}
              aria-pressed={current}
              onClick={() => setDayKey(item.key)}
              aria-describedby={error ? errorId : undefined}
              className={cn(
                "rounded-full border px-3 py-1.5 text-sm font-medium",
                current ? "border-primary bg-primary text-primary-foreground" : "hover:bg-muted",
              )}
            >
              {item.label}
            </button>
          );
        })}
      </div>
      {day && (
        <div role="group" aria-label={`Times on ${day.label}`} className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          {day.times.map((time) => {
            const current = time.start === picked?.start;
            return (
              <button
                key={time.start}
                type="button"
                aria-pressed={current}
                onClick={() => onPick(time.start)}
                className={cn(
                  "flex flex-col items-center rounded-lg border px-2 py-2 text-[15px] font-medium leading-5",
                  current ? "border-primary bg-primary text-primary-foreground" : "hover:bg-muted",
                )}
              >
                {time.label}
                {time.note && (
                  <span className={cn("text-xs font-normal", current ? "text-primary-foreground" : "text-muted-foreground")}>
                    {time.note}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      )}
      {errorLine}
    </section>
  );
}
