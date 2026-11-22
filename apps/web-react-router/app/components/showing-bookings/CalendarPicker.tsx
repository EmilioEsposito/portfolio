import { useRef } from "react";
import { CircleAlert, CircleCheck, Loader2, RefreshCw } from "lucide-react";
import { Button } from "~/components/ui/button";
import {
  type AgentFormState,
  type PickerGroup,
  type PickerRow,
  MAX_CALENDARS,
  MAX_LISTED,
  ROW_STATES,
  pickerGroups,
  pickerProblem,
  selectedCalendars,
  selectionLine,
  unreadableLine,
} from "~/lib/showing-calendar-picker";
import { cn } from "~/lib/utils";

// A list longer than this scrolls in its own box, so the form's buttons stay in reach.
const LONG = 20;
const AMBER = "text-amber-800 dark:text-amber-300";

function RowState({ state }: { state: PickerRow["state"] }) {
  return (
    <span
      className={cn(
        "flex shrink-0 items-center gap-1 text-xs font-medium",
        state === "readable" && "text-green-800 dark:text-green-400",
        state === "unreadable" && AMBER,
        state === "unchecked" && "text-muted-foreground",
      )}
    >
      {state === "readable" && <CircleCheck className="size-3.5" aria-hidden="true" />}
      {state === "unreadable" && <CircleAlert className="size-3.5" aria-hidden="true" />}
      {ROW_STATES[state]}
    </span>
  );
}

function Row({
  row,
  full,
  onTick,
}: {
  row: PickerRow;
  // No more calendars can be ticked.
  full: boolean;
  onTick: (calendarId: string, ticked: boolean) => void;
}) {
  return (
    <li>
      <label
        className={cn(
          "flex cursor-pointer items-start gap-3 px-3 py-2.5",
          row.ticked && row.state === "unreadable" ? "bg-amber-50 dark:bg-amber-950/30" : "hover:bg-muted/50",
        )}
      >
        <input
          type="checkbox"
          className="mt-0.5 size-4 shrink-0"
          checked={row.ticked}
          disabled={full && !row.ticked}
          onChange={(event) => onTick(row.calendar_id, event.target.checked)}
        />
        <span className="flex min-w-0 flex-1 flex-col gap-x-3 gap-y-0.5 sm:flex-row sm:items-start sm:justify-between">
          <span className="min-w-0 text-sm">
            <span className="block break-words font-medium">{row.name}</span>
            {row.address && <span className="block break-all text-xs text-muted-foreground">{row.address}</span>}
          </span>
          <RowState state={row.state} />
        </span>
      </label>
    </li>
  );
}

function Group({
  id,
  group,
  full,
  onTick,
}: {
  id: string;
  group: PickerGroup;
  full: boolean;
  onTick: (calendarId: string, ticked: boolean) => void;
}) {
  const heading = `${id}-${group.key}-heading`;
  return (
    <div role="group" aria-labelledby={heading} className="border-t first:border-t-0">
      <p id={heading} className="break-words bg-muted/50 px-3 py-1.5 text-xs font-semibold">
        {group.heading}
      </p>
      {group.note && <p className="px-3 py-2.5 text-sm leading-6 text-muted-foreground">{group.note}</p>}
      {group.rows.length > 0 && (
        <ul className="divide-y">
          {group.rows.map((row) => (
            <Row key={row.calendar_id.toLowerCase()} row={row} full={full} onTick={onTick} />
          ))}
        </ul>
      )}
    </div>
  );
}

/** The calendars that block an agent's showings: the ones the team's connection can read, to tick. */
export function CalendarPicker({
  id,
  form,
  onTick,
  onRefresh,
}: {
  id: string;
  form: AgentFormState;
  onTick: (calendarId: string, ticked: boolean) => void;
  onRefresh: () => void;
}) {
  const refreshRef = useRef<HTMLButtonElement>(null);
  const list = form.calendars;
  const account = list?.account ?? "the connected account";
  const groups = pickerGroups(form);
  const selected = selectedCalendars(form).length;
  const problem = list && pickerProblem(list);
  const warning = unreadableLine(groups);
  const listed = groups.reduce((count, group) => count + group.rows.length, 0);

  function tryAgain() {
    // This button goes away when the answer arrives; Refresh does the same and stays.
    refreshRef.current?.focus();
    onRefresh();
  }

  return (
    <fieldset aria-describedby={`${id}-hint`} className="min-w-0 space-y-2">
      <legend className="sr-only">Calendars that block showings</legend>
      <div className="flex items-center justify-between gap-3">
        <span aria-hidden="true" className="text-sm font-medium leading-5">
          Calendars that block showings
        </span>
        <Button
          ref={refreshRef}
          type="button"
          variant="outline"
          size="sm"
          className="shrink-0 gap-2"
          aria-disabled={form.reading}
          aria-label="Refresh the calendars"
          onClick={() => {
            if (!form.reading) onRefresh();
          }}
        >
          <RefreshCw className={cn("size-4", form.reading && "animate-spin")} aria-hidden="true" />
          Refresh
        </Button>
      </div>
      <p id={`${id}-hint`} className="break-words text-xs leading-5 text-muted-foreground">
        Calendars {account} can read
      </p>

      {form.readError && (
        <div role="alert" className="rounded-lg border border-destructive/50 p-3 text-sm leading-6">
          <p className="break-words">{form.readError}</p>
          <Button type="button" variant="outline" size="sm" className="mt-2" onClick={tryAgain}>
            Try again
          </Button>
        </div>
      )}
      {problem && (
        <div
          role={problem.retry ? "alert" : "status"}
          className="rounded-lg bg-amber-50 p-3 text-sm leading-6 text-amber-900 dark:bg-amber-950/30 dark:text-amber-200"
        >
          <p className="break-words">{problem.message}</p>
          {problem.retry && (
            <Button type="button" variant="outline" size="sm" className="mt-2" onClick={tryAgain}>
              Try again
            </Button>
          )}
        </div>
      )}
      {form.reading && !list && (
        <p role="status" className="flex items-center gap-2 text-sm">
          <Loader2 className="size-4 animate-spin" aria-hidden="true" />
          Loading calendars…
        </p>
      )}

      {groups.length > 0 && (
        <div className={cn("overflow-hidden rounded-lg border", listed > LONG && "max-h-[36rem] overflow-y-auto")}>
          {groups.map((group) => (
            <Group key={group.key} id={id} group={group} full={selected >= MAX_CALENDARS} onTick={onTick} />
          ))}
        </div>
      )}
      {list?.truncated && (
        <p className="text-xs text-muted-foreground">
          There are more calendars than are listed here, which is at most {MAX_LISTED}.
        </p>
      )}

      <div role="status" className="space-y-1 text-sm leading-6">
        <p>{selectionLine(selected)}</p>
        {warning && <p className={AMBER}>{warning}</p>}
      </div>

      <details className="text-sm">
        <summary className="w-fit cursor-pointer text-muted-foreground underline underline-offset-4 focus-visible:outline focus-visible:outline-2">
          Don&rsquo;t see a calendar?
        </summary>
        <ol className="mt-2 list-decimal space-y-1 break-words pl-5 leading-6">
          <li>Its owner shares it with {account} as &ldquo;See only free/busy&rdquo;.</li>
          <li>Someone signs in to Google as {account} and accepts the share.</li>
          <li>Press Refresh.</li>
        </ol>
      </details>
    </fieldset>
  );
}
