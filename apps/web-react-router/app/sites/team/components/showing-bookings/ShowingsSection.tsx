import { useCallback, useEffect, useState } from "react";
import { CircleAlert, FlaskConical, Loader2, RefreshCw } from "lucide-react";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import {
  type AgentCalendar,
  type BookedPerson,
  type BookedShowing,
  type CalendarStatus,
  type SettingsView,
  type ShowingsView,
  type UnitOffers,
  CALENDAR_STATUS_LABELS,
  OFFERS_SHOWN,
  describeAttention,
  describeOffer,
  describeRemoval,
  describeResidentAttention,
  describeShowingCancel,
  formatWhen,
  heldBackNote,
  leadAgentLine,
} from "~/sites/team/lib/showing-bookings";
import {
  type Attendance,
  type HostChangeResult,
  describeAttendance,
  describeHostChange,
} from "~/sites/team/lib/showing-team-actions";
import { cn } from "~/lib/utils";
import { AddBooking, AttendanceMarks, HostPicker } from "./TeamActions";
import { useShowingBookingsApi } from "./use-showing-bookings-api";

const STATUS_STYLES: Record<CalendarStatus, string> = {
  read: "border-green-300 text-green-800 dark:border-green-900 dark:text-green-400",
  sample: "border-amber-300 text-amber-800 dark:border-amber-900 dark:text-amber-300",
  unreadable: "border-red-300 text-red-700 dark:border-red-900 dark:text-red-400",
  inactive: "border-muted-foreground/40 text-muted-foreground",
};

function UnitItem({ unit }: { unit: UnitOffers }) {
  const [all, setAll] = useState(false);
  const shown = all ? unit.offers : unit.offers.slice(0, OFFERS_SHOWN);
  return (
    <li className="rounded-lg border p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="font-medium">
          {unit.unit}
          {unit.address && <span className="font-normal text-muted-foreground"> · {unit.address}</span>}
        </p>
        <p className="text-xs text-muted-foreground">
          {unit.offers.length} {unit.offers.length === 1 ? "time" : "times"} · {unit.notice_hours} hours&rsquo; notice
        </p>
      </div>
      {unit.offers.length === 0 ? (
        <p className="mt-2 flex items-start gap-2 rounded-md bg-amber-50 p-2 text-sm text-amber-900 dark:bg-amber-950/30 dark:text-amber-200">
          <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          <span>
            <span className="font-medium">No open time.</span> {unit.why_none}
          </span>
        </p>
      ) : (
        <>
          <ol className="mt-2 divide-y text-sm" aria-label={`Times ${unit.unit} offers, in the order they are offered`}>
            {shown.map((offer) => (
              <li key={offer.start} className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 py-1.5">
                <span className="tabular-nums">{formatWhen(offer.start)}</span>
                <span className="flex items-center gap-2 text-muted-foreground">
                  <Badge
                    variant="outline"
                    className={cn(
                      "font-normal",
                      offer.kind === "join" && "border-sky-300 text-sky-800 dark:border-sky-900 dark:text-sky-300",
                      offer.kind === "new" && offer.preferred && "border-violet-300 text-violet-800 dark:border-violet-900 dark:text-violet-300",
                    )}
                  >
                    {describeOffer(offer)}
                  </Badge>
                  {offer.host_name}
                </span>
              </li>
            ))}
          </ol>
          {unit.offers.length > OFFERS_SHOWN && (
            <Button variant="link" size="sm" className="mt-1 h-auto px-0" onClick={() => setAll(!all)}>
              {all ? "Show fewer" : `Show all ${unit.offers.length}`}
            </Button>
          )}
        </>
      )}
    </li>
  );
}

type CancelResult = { showing_id: number; showing_status: string; attendees_cancelled: number; texts_queued: number };

function BookedItem({
  showing,
  busy,
  onCancel,
  onRemove,
  agents,
  onMove,
  onMark,
}: {
  showing: BookedShowing;
  busy: boolean;
  onCancel: () => void;
  onRemove: (person: BookedPerson) => void;
  agents: AgentCalendar[];
  onMove: (agentId: number) => void;
  onMark: (person: BookedPerson, attendance: Attendance) => void;
}) {
  const label = `${showing.unit} ${formatWhen(showing.starts_at)}`;
  return (
    <li className="space-y-2 px-3 py-2.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span>
          <span className="font-medium">{showing.unit}</span>{" "}
          <span className="tabular-nums">{formatWhen(showing.starts_at)}</span>
          {showing.started && <span className="text-muted-foreground"> · started</span>}
        </span>
        <span className="flex flex-wrap items-center gap-2 text-muted-foreground">
          {showing.host_name} · {showing.attendees} of {showing.capacity}{" "}
          {showing.capacity === 1 ? "place" : "places"} taken
          {!showing.started && <HostPicker showing={showing} agents={agents} busy={busy} onMove={onMove} />}
          {!showing.started && (
            <Button
              variant="outline"
              size="sm"
              className="h-7"
              disabled={busy}
              aria-label={`Cancel the showing of ${label}`}
              onClick={onCancel}
            >
              Cancel showing
            </Button>
          )}
        </span>
      </div>
      {showing.resident_attention && (
        <p className="flex items-center gap-1.5 text-amber-800 dark:text-amber-300">
          <CircleAlert className="size-3.5 shrink-0" aria-hidden="true" />
          {describeResidentAttention(showing.resident_attention)}
        </p>
      )}
      {showing.people.length > 0 && (
        <ul className="space-y-1.5" aria-label={`People booked for ${label}`}>
          {showing.people.map((person) => (
            <li key={person.id} className="flex flex-wrap items-start justify-between gap-x-3 gap-y-1 rounded-md bg-muted/40 px-2.5 py-1.5">
              <span className="min-w-0">
                <span className="font-medium">{person.name}</span>{" "}
                <span className="tabular-nums text-muted-foreground">{person.mobile}</span>
                {person.email && <span className="break-all text-muted-foreground"> · {person.email}</span>}
                {!person.texts_accepted && <span className="text-muted-foreground"> · no texts</span>}
                {leadAgentLine(person) && (
                  <span className="mt-0.5 block text-muted-foreground">
                    {leadAgentLine(person)}
                    {person.lead_conversation_id && (
                      <>
                        {" · "}
                        <a
                          href={`/sernia-chat?id=${encodeURIComponent(person.lead_conversation_id)}`}
                          className="underline underline-offset-4"
                        >
                          conversation
                        </a>
                      </>
                    )}
                  </span>
                )}
                {person.attention_reason && (
                  <span className="mt-0.5 flex items-center gap-1.5 text-amber-800 dark:text-amber-300">
                    <CircleAlert className="size-3.5 shrink-0" aria-hidden="true" />
                    {describeAttention(person.attention_reason)}
                  </span>
                )}
              </span>
              {showing.started ? (
                <AttendanceMarks
                  person={person}
                  label={label}
                  busy={busy}
                  onMark={(attendance) => onMark(person, attendance)}
                />
              ) : (
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-7"
                  disabled={busy}
                  aria-label={`Remove ${person.name} from ${label}`}
                  onClick={() => onRemove(person)}
                >
                  Remove
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

export function ShowingsSection({
  settings,
  onChanged,
}: {
  /** The saved settings, so a confirmation can say what is not really sent; null until they are read. */
  settings: SettingsView | null;
  /** Something here queued messages, so the lists of messages are out of date. */
  onChanged: () => void;
}) {
  const api = useShowingBookingsApi();
  const [view, setView] = useState<ShowingsView | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [acting, setActing] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionDone, setActionDone] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    const result = await api<ShowingsView>("/showings", {}, "Could not load the showings");
    if (result.ok) setView(result.body);
    else setLoadError(result.problems.length ? `${result.message} ${result.problems.join("; ")}` : result.message);
    setLoading(false);
  }, [api]);

  useEffect(() => {
    void load();
  }, [load]);

  const note = (unit: string) =>
    settings ? heldBackNote(settings.settings, settings.is_production, unit) : "";

  async function act(path: string, done: string) {
    setActing(path);
    setActionError(null);
    setActionDone(null);
    const result = await api<CancelResult>(path, { method: "POST" }, "Could not cancel");
    if (result.ok) {
      setActionDone(done);
      onChanged();
      await load();
    } else {
      setActionError(result.message);
    }
    setActing(null);
  }

  async function cancelShowing(showing: BookedShowing) {
    if (!view || !window.confirm(describeShowingCancel(showing, note(showing.unit)))) return;
    await act(`/showings/${showing.id}/cancel`, `Cancelled the showing of ${showing.unit} on ${formatWhen(showing.starts_at)}.`);
  }

  async function removePerson(showing: BookedShowing, person: BookedPerson) {
    if (!view || !window.confirm(describeRemoval(showing, person, note(showing.unit)))) return;
    await act(
      `/showings/${showing.id}/attendees/${person.id}/remove`,
      `Removed ${person.name} from the showing of ${showing.unit}.`,
    );
  }

  async function run<T>(key: string, path: string, init: RequestInit, fallback: string, done: (body: T) => string) {
    setActing(key);
    setActionError(null);
    setActionDone(null);
    const result = await api<T>(path, init, fallback);
    if (result.ok) {
      setActionDone(done(result.body));
      onChanged();
      await load();
    } else {
      setActionError(result.message);
    }
    setActing(null);
  }

  async function moveHost(showing: BookedShowing, agentId: number) {
    // Hosts are the team: nothing reaches a prospect, so no confirmation.
    await run<HostChangeResult>(
      `host-${showing.id}`,
      `/showings/${showing.id}/host`,
      { method: "POST", body: JSON.stringify({ agent_id: agentId }) },
      "Could not change the host",
      describeHostChange,
    );
  }

  async function markAttendance(showing: BookedShowing, person: BookedPerson, attendance: Attendance) {
    await run(
      `attendance-${person.id}`,
      `/showings/${showing.id}/attendees/${person.id}/attendance`,
      { method: "PUT", body: JSON.stringify({ attendance }) },
      "Could not mark who came",
      () => describeAttendance(person, attendance),
    );
  }

  async function booked(message: string) {
    setActionError(null);
    setActionDone(message);
    onChanged();
    await load();
  }

  const times = view?.open_times;
  return (
    <section aria-labelledby="showings-heading" className="rounded-xl border p-5 sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 id="showings-heading" className="text-xl font-semibold">
            Showings
          </h3>
          <p className="mt-1 text-sm leading-6 text-muted-foreground">
            The times each unit on offer is offering right now, in the order they are offered: joins first, then
            preferred times, then the rest. Then the showings booked from the last 3 days to 14 days ahead, with who
            is coming: add a booking for someone who called, change a showing&rsquo;s host, and mark who came once it
            has started.
          </p>
        </div>
        <Button variant="outline" size="sm" className="gap-1.5" disabled={loading} onClick={() => void load()}>
          <RefreshCw className={cn("size-3.5", loading && "animate-spin")} aria-hidden="true" />
          Refresh
        </Button>
      </div>

      {loading && !view && (
        <p role="status" className="mt-5 flex items-center gap-2 text-sm">
          <Loader2 className="size-4 animate-spin" aria-hidden="true" />
          Working out open times…
        </p>
      )}
      {loadError && (
        <div role="alert" className="mt-4 rounded-lg border border-destructive/50 p-3 text-sm">
          <p>{loadError}</p>
          <Button variant="outline" size="sm" className="mt-2" disabled={loading} onClick={() => void load()}>
            Try again
          </Button>
        </div>
      )}

      {view && times && (
        <>
          {times.sample_calendar_in_use && (
            <p className="mt-4 flex items-start gap-2 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm leading-6 text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200">
              <FlaskConical className="mt-1 size-4 shrink-0" aria-hidden="true" />
              <span>
                <strong className="font-medium">Sample calendar in use.</strong> No usable calendar connection reads
                some agents here, so their busy times come from a made-up week. Their times below are not real. This
                never happens in production.
              </span>
            </p>
          )}

          <h4 className="mt-5 text-sm font-semibold">Agents&rsquo; calendars</h4>
          {times.agents.length === 0 ? (
            <p className="mt-1 text-sm text-muted-foreground">No showing agents. Add one under Showing agents.</p>
          ) : (
            <ul className="mt-2 space-y-1.5 text-sm">
              {times.agents.map((agent) => (
                <li key={agent.agent_id} className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">{agent.name}</span>
                  <Badge variant="outline" className={cn("font-normal", STATUS_STYLES[agent.status])}>
                    {CALENDAR_STATUS_LABELS[agent.status]}
                  </Badge>
                  {agent.connection_email && agent.status === "read" && (
                    <span className="break-all text-muted-foreground">through {agent.connection_email}</span>
                  )}
                  {agent.problem && <span className="text-red-700 dark:text-red-400">{agent.problem}</span>}
                </li>
              ))}
            </ul>
          )}

          <h4 className="mt-5 text-sm font-semibold">Open times by unit</h4>
          {times.units.length === 0 ? (
            <p className="mt-1 text-sm text-muted-foreground">
              No unit is on offer. Units and their off-market dates are under Rental listing data.
            </p>
          ) : (
            <ul className="mt-2 space-y-3">
              {times.units.map((unit) => (
                <UnitItem key={unit.unit} unit={unit} />
              ))}
            </ul>
          )}

          <h4 className="mt-5 text-sm font-semibold">Booked, from the last 3 days to 14 days ahead</h4>
          <AddBooking units={times.units} note={note} api={api} onBooked={booked} />
          {view.booked.length === 0 ? (
            <p className="mt-1 text-sm text-muted-foreground">No showings are booked.</p>
          ) : (
            <ul className="mt-2 divide-y rounded-lg border text-sm">
              {view.booked.map((showing) => (
                <BookedItem
                  key={showing.id}
                  showing={showing}
                  busy={acting !== null}
                  onCancel={() => void cancelShowing(showing)}
                  onRemove={(person) => void removePerson(showing, person)}
                  agents={times.agents}
                  onMove={(agentId) => void moveHost(showing, agentId)}
                  onMark={(person, attendance) => void markAttendance(showing, person, attendance)}
                />
              ))}
            </ul>
          )}
          <p role="status" className="mt-2 text-sm font-medium">
            {actionError ? <span className="text-destructive">{actionError}</span> : actionDone}
          </p>
          <p className="mt-4 text-xs text-muted-foreground">Times are Eastern. Worked out {formatWhen(times.now)}.</p>
        </>
      )}
    </section>
  );
}
