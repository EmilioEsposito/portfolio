import { useState } from "react";
import { Loader2, Plus } from "lucide-react";
import { Button } from "~/components/ui/button";
import { Input } from "~/components/ui/input";
import { Label } from "~/components/ui/label";
import {
  type AgentCalendar,
  type BookedPerson,
  type BookedShowing,
  type UnitOffers,
  formatWhen,
} from "~/lib/showing-bookings";
import {
  type Attendance,
  type TeamBookingForm,
  type TeamBookingResult,
  ATTENDANCE_LABELS,
  attendanceAfter,
  bookingBody,
  describeBookingResult,
  describeTeamBooking,
  emptyBookingForm,
  hostChoices,
  missingFields,
  timeOptions,
} from "~/lib/showing-team-actions";
import { cn } from "~/lib/utils";
import type { ApiResult } from "./use-showing-bookings-api";

const SELECT =
  "flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:opacity-50";

type Api = <T>(path: string, init?: RequestInit, fallback?: string) => Promise<ApiResult<T>>;

function FieldError({ id, message }: { id: string; message?: string }) {
  if (!message) return null;
  return (
    <p id={id} className="text-xs text-destructive">
      {message}
    </p>
  );
}

/** "Add a booking" for someone who called: a unit, one of its open times, and the caller. */
export function AddBooking({
  units,
  note,
  api,
  onBooked,
}: {
  units: UnitOffers[];
  /** What is not really sent for a unit, for the question before booking. */
  note: (unit: string) => string;
  api: Api;
  onBooked: (message: string) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<TeamBookingForm>(() => emptyBookingForm(units));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fields, setFields] = useState<Record<string, string>>({});

  if (!open) {
    return (
      <Button
        variant="outline"
        size="sm"
        className="mt-2 gap-1.5"
        disabled={units.length === 0}
        onClick={() => {
          setForm(emptyBookingForm(units));
          setOpen(true);
        }}
      >
        <Plus className="size-3.5" aria-hidden="true" />
        Add a booking
      </Button>
    );
  }

  const set = <K extends keyof TeamBookingForm>(key: K, value: TeamBookingForm[K]) => {
    setForm((current) => ({ ...current, [key]: value, ...(key === "unit" ? { start: "" } : {}) }));
    setFields((current) => ({ ...current, [key]: "" }));
  };
  const options = timeOptions(units, form.unit);
  const missing = missingFields(form);
  const hasMobile = form.mobile.trim() !== "";

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (missing.length) {
      setError(`Add ${missing.join(", ")}.`);
      return;
    }
    const question = describeTeamBooking(form, note(form.unit));
    if (question && !window.confirm(question)) return;
    setSaving(true);
    setError(null);
    setFields({});
    const result = await api<TeamBookingResult>(
      "/showings/bookings",
      { method: "POST", body: JSON.stringify(bookingBody(form)) },
      "Could not book",
    );
    setSaving(false);
    if (!result.ok) {
      setError(result.message);
      setFields(result.fields);
      return;
    }
    setOpen(false);
    await onBooked(describeBookingResult(result.body, form));
  }

  return (
    <form onSubmit={(event) => void submit(event)} className="mt-2 space-y-3 rounded-lg border p-4" aria-label="Add a booking">
      <p className="text-sm font-medium">Add a booking for someone who called</p>
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1">
          <Label htmlFor="team-booking-unit">Unit</Label>
          <select id="team-booking-unit" className={SELECT} value={form.unit} onChange={(e) => set("unit", e.target.value)}>
            {units.map((unit) => (
              <option key={unit.unit} value={unit.unit}>
                {unit.unit}
                {unit.address ? ` · ${unit.address}` : ""}
              </option>
            ))}
          </select>
        </div>
        <div className="space-y-1">
          <Label htmlFor="team-booking-start">Time</Label>
          <select
            id="team-booking-start"
            className={SELECT}
            value={form.start}
            disabled={options.length === 0}
            aria-invalid={Boolean(fields.start) || undefined}
            aria-describedby={fields.start ? "team-booking-start-error" : undefined}
            onChange={(e) => set("start", e.target.value)}
          >
            <option value="">{options.length ? "Pick a time" : "No open time"}</option>
            {options.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          <FieldError id="team-booking-start-error" message={fields.start} />
        </div>
        <div className="space-y-1 sm:col-span-2">
          <Label htmlFor="team-booking-name">Name</Label>
          <Input
            id="team-booking-name"
            value={form.name}
            autoComplete="off"
            aria-invalid={Boolean(fields.name) || undefined}
            aria-describedby={fields.name ? "team-booking-name-error" : undefined}
            onChange={(e) => set("name", e.target.value)}
          />
          <FieldError id="team-booking-name-error" message={fields.name} />
        </div>
        <div className="space-y-1">
          <Label htmlFor="team-booking-mobile">
            Mobile <span className="font-normal text-muted-foreground">(optional)</span>
          </Label>
          <Input
            id="team-booking-mobile"
            type="tel"
            inputMode="tel"
            autoComplete="off"
            value={form.mobile}
            aria-invalid={Boolean(fields.mobile) || undefined}
            aria-describedby={fields.mobile ? "team-booking-mobile-error" : undefined}
            onChange={(e) => set("mobile", e.target.value)}
          />
          <FieldError id="team-booking-mobile-error" message={fields.mobile} />
        </div>
        <div className="space-y-1">
          <Label htmlFor="team-booking-email">
            Email <span className="font-normal text-muted-foreground">(optional)</span>
          </Label>
          <Input
            id="team-booking-email"
            type="email"
            autoComplete="off"
            value={form.email}
            aria-invalid={Boolean(fields.email) || undefined}
            aria-describedby={fields.email ? "team-booking-email-error" : undefined}
            onChange={(e) => set("email", e.target.value)}
          />
          <FieldError id="team-booking-email-error" message={fields.email} />
        </div>
      </div>
      <div className="flex items-start gap-2">
        <input
          id="team-booking-texts"
          type="checkbox"
          className="mt-1 size-4"
          checked={form.texts && hasMobile}
          disabled={!hasMobile}
          aria-describedby="team-booking-texts-help"
          onChange={(e) => set("texts", e.target.checked)}
        />
        <div>
          <Label htmlFor="team-booking-texts" className={cn(!hasMobile && "text-muted-foreground")}>
            They agreed to texts about this showing
          </Label>
          <p id="team-booking-texts-help" className="text-xs text-muted-foreground">
            {hasMobile
              ? "Ticked: a confirmation text now, and the calendar invite once it is delivered. Not ticked: no texts at all."
              : "Add a mobile to text them. Without one they get no texts."}
          </p>
        </div>
      </div>
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      <div className="flex gap-2">
        <Button type="submit" size="sm" disabled={saving}>
          {saving && <Loader2 className="mr-1.5 size-3.5 animate-spin" aria-hidden="true" />}
          Book
        </Button>
        <Button type="button" variant="ghost" size="sm" disabled={saving} onClick={() => setOpen(false)}>
          Close
        </Button>
      </div>
    </form>
  );
}

/** "Change host": the active agents other than the host. The server checks they are free. */
export function HostPicker({
  showing,
  agents,
  busy,
  onMove,
}: {
  showing: BookedShowing;
  agents: AgentCalendar[];
  busy: boolean;
  onMove: (agentId: number) => void;
}) {
  const [choice, setChoice] = useState("");
  const choices = hostChoices(agents, showing);
  if (choices.length === 0) return null;
  const id = `host-${showing.id}`;
  return (
    <span className="flex items-center gap-1.5">
      <label htmlFor={id} className="sr-only">
        Change the host of {showing.unit} {formatWhen(showing.starts_at)}
      </label>
      <select
        id={id}
        className={cn(SELECT, "h-7 w-auto py-0 text-xs")}
        value={choice}
        disabled={busy}
        onChange={(e) => setChoice(e.target.value)}
      >
        <option value="">Change host…</option>
        {choices.map((agent) => (
          <option key={agent.id} value={agent.id}>
            {agent.name}
          </option>
        ))}
      </select>
      <Button
        variant="outline"
        size="sm"
        className="h-7"
        disabled={busy || !choice}
        onClick={() => {
          onMove(Number(choice));
          setChoice("");
        }}
      >
        Move
      </Button>
    </span>
  );
}

/** Came / Did not come, once the showing has started. Pressing the set one clears it. */
export function AttendanceMarks({
  person,
  label,
  busy,
  onMark,
}: {
  person: BookedPerson;
  label: string;
  busy: boolean;
  onMark: (attendance: Attendance) => void;
}) {
  return (
    <span role="group" aria-label={`Did ${person.name} come to ${label}?`} className="flex gap-1">
      {(["attended", "no_show"] as const).map((mark) => {
        const pressed = person.attendance === mark;
        return (
          <Button
            key={mark}
            variant={pressed ? "default" : "outline"}
            size="sm"
            className="h-7"
            aria-pressed={pressed}
            disabled={busy}
            onClick={() => onMark(attendanceAfter(person.attendance, mark))}
          >
            {ATTENDANCE_LABELS[mark]}
          </Button>
        );
      })}
    </span>
  );
}
