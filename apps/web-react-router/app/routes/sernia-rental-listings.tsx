import type { Route } from "./+types/sernia-rental-listings";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router";
import { useAuth } from "@clerk/react-router";
import {
  ArrowLeft,
  Building,
  Globe,
  History,
  Loader2,
  Lock,
  Menu,
  Plus,
  RotateCcw,
  Save,
  X,
} from "lucide-react";
import { AuthGuard } from "~/components/auth-guard";
import { ConversationSidebar } from "~/components/sernia/conversation-sidebar";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "~/components/ui/card";
import { Input } from "~/components/ui/input";
import { Label } from "~/components/ui/label";
import { SidebarInset, SidebarProvider, useSidebar } from "~/components/ui/sidebar";
import { Switch } from "~/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "~/components/ui/tabs";
import {
  type Change,
  type PreferredWindow,
  type RentalData,
  type RentalListing,
  type ShowingRules,
  SHOWING_RULES_SUBJECT,
  WEEKDAY_LABELS,
  WEEKDAYS,
  cleanRules,
  cleanListing,
  describeHistory,
  isOffMarket,
  localToday,
  newListing,
  problemsFrom,
  sameRules,
  sameListing,
  shortTime,
} from "~/lib/rental-listings";
import { cn } from "~/lib/utils";

const API_BASE = "/api/rental-listings";
const HISTORY_LIMIT = 50;

export function meta({}: Route.MetaArgs) {
  return [
    { title: "Rental Listings | Sernia" },
    {
      name: "description",
      content: "Edit each unit's listing and the showing rules.",
    },
  ];
}

// ---------------------------------------------------------------------------
// Talking to the API
// ---------------------------------------------------------------------------

type Refusal = { message: string; problems: string[] };
type Result<T> = { ok: true; body: T } | { ok: false; refusal: Refusal; status: number };

function useApi() {
  const { getToken } = useAuth();
  return useCallback(
    async <T,>(path: string, init: RequestInit = {}): Promise<Result<T>> => {
      try {
        const token = await getToken();
        const response = await fetch(`${API_BASE}${path}`, {
          ...init,
          headers: {
            Authorization: `Bearer ${token}`,
            ...(init.body ? { "Content-Type": "application/json" } : {}),
          },
        });
        const body = await response.json().catch(() => null);
        if (response.ok) return { ok: true, body: body as T };
        return { ok: false, status: response.status, refusal: problemsFrom(response.status, body) };
      } catch (error) {
        const message = error instanceof Error ? error.message : "Network error";
        return { ok: false, status: 0, refusal: { message, problems: [] } };
      }
    },
    [getToken],
  );
}

// ---------------------------------------------------------------------------
// Small pieces
// ---------------------------------------------------------------------------

function RefusalBox({ refusal, onReload }: { refusal: Refusal; onReload?: () => void }) {
  return (
    <div
      role="alert"
      className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/30 dark:text-red-400"
    >
      <p className="font-medium">{refusal.message}</p>
      {refusal.problems.length > 0 && (
        <ul className="mt-1 list-disc space-y-0.5 pl-5">
          {refusal.problems.map((problem) => (
            <li key={problem}>{problem}</li>
          ))}
        </ul>
      )}
      {onReload && (
        <Button variant="outline" size="sm" className="mt-2" onClick={onReload}>
          Load what is on file
        </Button>
      )}
    </div>
  );
}

function SavedNote({ show }: { show: boolean }) {
  return (
    <p aria-live="polite" className="text-sm text-green-700 dark:text-green-400">
      {show ? "Saved." : ""}
    </p>
  );
}

function Field({
  id,
  label,
  hint,
  children,
  className,
}: {
  id: string;
  label: string;
  hint?: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("space-y-1.5", className)}>
      <Label htmlFor={id}>{label}</Label>
      {children}
      {hint && (
        <p id={`${id}-hint`} className="text-xs text-muted-foreground">
          {hint}
        </p>
      )}
    </div>
  );
}

/**
 * Kept at the bottom of the screen while there is something to save or say,
 * so Save and the reasons for a refusal are in reach wherever the person is
 * editing.
 */
function SaveBar({
  dirty,
  saving,
  onSave,
  onReset,
  refusal,
  justSaved,
  onReload,
  saveLabel = "Save",
  resetLabel = "Undo edits",
}: {
  dirty: boolean;
  saving: boolean;
  onSave: () => void;
  onReset: () => void;
  refusal: { refusal: Refusal; stale: boolean } | null;
  justSaved: boolean;
  onReload: () => void;
  saveLabel?: string;
  resetLabel?: string;
}) {
  if (!dirty && !refusal && !justSaved) return null;
  return (
    <div className="sticky bottom-2 z-10 space-y-2 rounded-lg border bg-background/95 p-3 shadow-md backdrop-blur supports-[backdrop-filter]:bg-background/80">
      {refusal && (
        <RefusalBox
          refusal={refusal.refusal}
          onReload={refusal.stale ? onReload : undefined}
        />
      )}
      <div className="flex flex-wrap items-center justify-end gap-2">
        <div className="mr-auto">
          <SavedNote show={justSaved && !dirty} />
          {dirty && <p className="text-sm text-muted-foreground">Edits not saved yet.</p>}
        </div>
        {dirty && (
          <Button variant="outline" size="sm" className="gap-1.5" onClick={onReset} disabled={saving}>
            <RotateCcw className="h-3.5 w-3.5" />
            {resetLabel}
          </Button>
        )}
        <Button size="sm" className="gap-1.5" onClick={onSave} disabled={!dirty || saving}>
          {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
          {saveLabel}
        </Button>
      </div>
    </div>
  );
}

/** One line per fact or note, each its own input. Blank lines are dropped on save. */
function LinesEditor({
  id,
  lines,
  onChange,
  addLabel,
  placeholder,
}: {
  id: string;
  lines: string[];
  onChange: (lines: string[]) => void;
  addLabel: string;
  placeholder: string;
}) {
  const setLine = (index: number, value: string) =>
    onChange(lines.map((line, at) => (at === index ? value : line)));
  const kind = addLabel.replace(/^Add /, "");
  const lineLabel = kind.charAt(0).toUpperCase() + kind.slice(1);
  return (
    <div className="space-y-2">
      {lines.length === 0 && <p className="text-sm text-muted-foreground">None yet.</p>}
      {lines.map((line, index) => (
        <div key={index} className="flex items-center gap-2">
          <Input
            id={`${id}-${index}`}
            aria-label={`${lineLabel} ${index + 1}`}
            value={line}
            maxLength={300}
            placeholder={placeholder}
            onChange={(event) => setLine(index, event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                onChange([...lines.slice(0, index + 1), "", ...lines.slice(index + 1)]);
                requestAnimationFrame(() => document.getElementById(`${id}-${index + 1}`)?.focus());
              }
            }}
          />
          <Button
            variant="ghost"
            size="icon"
            className="h-9 w-9 shrink-0"
            aria-label={`Remove “${line || "blank line"}”`}
            onClick={() => onChange(lines.filter((_, at) => at !== index))}
          >
            <X className="h-4 w-4" />
          </Button>
        </div>
      ))}
      <Button
        variant="outline"
        size="sm"
        className="gap-1.5"
        onClick={() => {
          onChange([...lines, ""]);
          requestAnimationFrame(() => document.getElementById(`${id}-${lines.length}`)?.focus());
        }}
      >
        <Plus className="h-3.5 w-3.5" />
        {addLabel}
      </Button>
    </div>
  );
}

function DayToggles({
  days,
  onChange,
  label,
}: {
  days: string[];
  onChange: (days: (typeof WEEKDAYS)[number][]) => void;
  label: string;
}) {
  return (
    <div role="group" aria-label={label} className="flex flex-wrap gap-1">
      {WEEKDAYS.map((day) => {
        const on = days.includes(day);
        return (
          <button
            key={day}
            type="button"
            aria-pressed={on}
            onClick={() =>
              onChange(WEEKDAYS.filter((d) => (d === day ? !on : days.includes(d))))
            }
            className={cn(
              "min-w-[44px] rounded-md border px-2 py-1 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              on
                ? "border-primary bg-primary text-primary-foreground"
                : "border-input bg-background text-muted-foreground hover:bg-accent hover:text-accent-foreground",
            )}
          >
            {WEEKDAY_LABELS[day]}
          </button>
        );
      })}
    </div>
  );
}

function WindowsEditor({
  windows,
  onChange,
}: {
  windows: PreferredWindow[];
  onChange: (windows: PreferredWindow[]) => void;
}) {
  const setWindow = (index: number, patch: Partial<PreferredWindow>) =>
    onChange(windows.map((window, at) => (at === index ? { ...window, ...patch } : window)));
  return (
    <div className="space-y-3">
      {windows.length === 0 && (
        <p className="text-sm text-muted-foreground">No preference: any time in the showing window.</p>
      )}
      {windows.map((window, index) => (
        <div key={index} className="space-y-2 rounded-md border p-3">
          <div className="flex items-start justify-between gap-2">
            <DayToggles
              label={`Days for preferred time ${index + 1}`}
              days={window.days}
              onChange={(days) => setWindow(index, { days })}
            />
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8 shrink-0"
              aria-label={`Remove preferred time ${index + 1}`}
              onClick={() => onChange(windows.filter((_, at) => at !== index))}
            >
              <X className="h-4 w-4" />
            </Button>
          </div>
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <Label htmlFor={`window-${index}-start`} className="sr-only">
              Preferred time {index + 1} from
            </Label>
            <Input
              id={`window-${index}-start`}
              type="time"
              className="w-32"
              value={shortTime(window.start)}
              onChange={(event) => setWindow(index, { start: event.target.value })}
            />
            <span className="text-muted-foreground">to</span>
            <Label htmlFor={`window-${index}-end`} className="sr-only">
              Preferred time {index + 1} last start
            </Label>
            <Input
              id={`window-${index}-end`}
              type="time"
              className="w-32"
              value={shortTime(window.end)}
              onChange={(event) => setWindow(index, { end: event.target.value })}
            />
          </div>
        </div>
      ))}
      <Button
        variant="outline"
        size="sm"
        className="gap-1.5"
        onClick={() => onChange([...windows, { days: ["mon", "tue", "wed", "thu", "fri"], start: "16:00", end: "18:00" }])}
      >
        <Plus className="h-3.5 w-3.5" />
        Add preferred time
      </Button>
    </div>
  );
}

function formatWhen(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function subjectLabel(subject: string): string {
  return subject === SHOWING_RULES_SUBJECT ? "Showing rules" : subject;
}

function HistoryList({
  changes,
  loading,
  showSubject = false,
}: {
  changes: Change[] | null;
  loading: boolean;
  showSubject?: boolean;
}) {
  if (loading && changes === null) {
    return <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" aria-label="Loading history" />;
  }
  if (!changes?.length) return <p className="text-sm text-muted-foreground">No saved changes yet.</p>;
  const described = describeHistory(changes, changes.length < HISTORY_LIMIT);
  return (
    <ol className="space-y-3">
      {described.map(({ change, lines }, index) => (
        <li key={`${change.changed_at}-${index}`} className="border-l-2 pl-3 text-sm">
          <p className="text-xs text-muted-foreground">
            {showSubject && <span className="font-medium text-foreground">{subjectLabel(change.subject)} · </span>}
            {formatWhen(change.changed_at)} · {change.changed_by}
            {change.source ? ` · ${change.source}` : ""}
          </p>
          <ul className="mt-0.5 space-y-0.5">
            {lines.map((line, at) => (
              <li key={at} className="break-words">
                {line}
              </li>
            ))}
          </ul>
        </li>
      ))}
    </ol>
  );
}

/** Saved changes for one subject, or everything when `subject` is null. Skipped when `enabled` is false. */
function useHistory(subject: string | null, refreshKey: number, enabled = true) {
  const api = useApi();
  const [changes, setChanges] = useState<Change[] | null>(null);
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    if (!enabled) return;
    let current = true;
    setLoading(true);
    const query = new URLSearchParams({ limit: String(HISTORY_LIMIT) });
    if (subject !== null) query.set("subject", subject);
    api<Change[]>(`/history?${query}`).then((result) => {
      if (!current) return;
      setChanges(result.ok ? result.body : []);
      setLoading(false);
    });
    return () => {
      current = false;
    };
  }, [api, subject, refreshKey, enabled]);
  return { changes, loading };
}

// ---------------------------------------------------------------------------
// A unit
// ---------------------------------------------------------------------------

function UnitEditor({
  original,
  isNew,
  sharedBookingUrl,
  onSaved,
  onCancel,
  onReload,
  onDirtyChange,
}: {
  original: RentalListing;
  isNew: boolean;
  sharedBookingUrl: string;
  onSaved: (unit: RentalListing) => void;
  onCancel: () => void;
  onReload: () => void;
  onDirtyChange: (dirty: boolean) => void;
}) {
  const api = useApi();
  const [draft, setDraft] = useState<RentalListing>(original);
  const [saving, setSaving] = useState(false);
  const [refusal, setRefusal] = useState<{ refusal: Refusal; stale: boolean } | null>(null);
  const [justSaved, setJustSaved] = useState(false);
  const [historyKey, setHistoryKey] = useState(0);
  const { changes, loading } = useHistory(original.unit, historyKey, !isNew);

  const dirty = isNew || !sameListing(draft, original);
  useEffect(() => onDirtyChange(dirty), [dirty, onDirtyChange]);

  const set = <K extends keyof RentalListing>(field: K, value: RentalListing[K]) => {
    setDraft((unit) => ({ ...unit, [field]: value }));
    setJustSaved(false);
  };
  const slug = original.unit.replace(/\W+/g, "-");
  const id = (field: string) => `unit-${slug}-${field}`;

  const save = async () => {
    setSaving(true);
    setRefusal(null);
    const listing = cleanListing(draft);
    const result = isNew
      ? await api<RentalListing>("/listings", { method: "POST", body: JSON.stringify({ listing }) })
      : await api<RentalListing>(`/listings/${encodeURIComponent(original.unit)}`, {
          method: "PUT",
          body: JSON.stringify({ listing, based_on: original }),
        });
    setSaving(false);
    if (!result.ok) {
      setRefusal({ refusal: result.refusal, stale: result.status === 409 && !isNew });
      return;
    }
    setDraft(result.body);
    setJustSaved(true);
    setHistoryKey((key) => key + 1);
    onSaved(result.body);
  };

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="pb-3">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div className="min-w-0">
              <CardTitle className="text-base">{isNew ? `New unit: ${original.unit}` : original.unit}</CardTitle>
              <CardDescription>
                {isNew ? "Not saved yet." : "Edits take effect for the lead agent as soon as they are saved."}
              </CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-6">
          <section aria-labelledby={id("offer-heading")} className="space-y-4">
            <h3 id={id("offer-heading")} className="text-sm font-semibold">
              What is on offer
            </h3>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field id={id("address")} label="Address" className="sm:col-span-2">
                <Input
                  id={id("address")}
                  value={draft.address}
                  maxLength={120}
                  onChange={(event) => set("address", event.target.value)}
                />
              </Field>
              <Field id={id("asking_rent")} label="Asking rent per month ($)" hint="The rent on offer for the next lease, not what the current resident pays.">
                <Input
                  id={id("asking_rent")}
                  aria-describedby={id("asking_rent-hint")}
                  type="number"
                  inputMode="numeric"
                  min={1}
                  step={1}
                  value={draft.asking_rent ?? ""}
                  onChange={(event) =>
                    set("asking_rent", event.target.value === "" ? null : Math.round(Number(event.target.value)))
                  }
                />
              </Field>
              <div className="flex items-center gap-3 self-start sm:pt-8">
                <Switch
                  id={id("virtual")}
                  checked={draft.virtual_tours_allowed}
                  onCheckedChange={(checked) => set("virtual_tours_allowed", checked)}
                />
                <Label htmlFor={id("virtual")}>Virtual tours allowed</Label>
              </div>
              <Field id={id("lease-earliest")} label="Next lease: earliest start">
                <Input
                  id={id("lease-earliest")}
                  type="date"
                  value={draft.next_lease_start_earliest ?? ""}
                  max={draft.next_lease_start_latest ?? undefined}
                  onChange={(event) => set("next_lease_start_earliest", event.target.value || null)}
                />
              </Field>
              <Field id={id("lease-latest")} label="Next lease: latest start">
                <Input
                  id={id("lease-latest")}
                  type="date"
                  value={draft.next_lease_start_latest ?? ""}
                  min={draft.next_lease_start_earliest ?? undefined}
                  onChange={(event) => set("next_lease_start_latest", event.target.value || null)}
                />
              </Field>
              <Field
                id={id("off-market")}
                label="Off market from"
                hint="From this date the unit is no longer on offer, usually because the next lease is signed. Clear it when the unit goes back on offer."
              >
                <div className="flex gap-2">
                  <Input
                    id={id("off-market")}
                    type="date"
                    aria-describedby={id("off-market-hint")}
                    value={draft.off_market_on ?? ""}
                    onChange={(event) => set("off_market_on", event.target.value || null)}
                  />
                  {draft.off_market_on && (
                    <Button variant="outline" size="sm" className="h-9" onClick={() => set("off_market_on", null)}>
                      Clear
                    </Button>
                  )}
                </div>
              </Field>
              <Field
                id={id("booking")}
                label="Showing booking link"
                hint={
                  sharedBookingUrl
                    ? "Where a lead books a showing of this unit. Leave empty to use the shared link in the showing rules."
                    : "Where a lead books a showing of this unit. No shared link is set in the showing rules."
                }
              >
                <Input
                  id={id("booking")}
                  type="url"
                  inputMode="url"
                  placeholder="https://"
                  aria-describedby={id("booking-hint")}
                  value={draft.showing_booking_url}
                  onChange={(event) => set("showing_booking_url", event.target.value)}
                />
              </Field>
            </div>
          </section>

          <section aria-labelledby={id("times-heading")} className="space-y-2">
            <h3 id={id("times-heading")} className="text-sm font-semibold">
              Preferred showing times
            </h3>
            <p className="text-xs text-muted-foreground">
              Start times we would like showings to land on. A nudge, never a limit: leads may still book any
              time in the showing window. The second time is the last preferred start.
            </p>
            <WindowsEditor windows={draft.preferred_showing_times} onChange={(windows) => set("preferred_showing_times", windows)} />
          </section>
        </CardContent>
      </Card>

      <p className="text-sm">
        <span className="font-medium">Public facts are told to leads. Internal notes are not.</span>{" "}
        <span className="text-muted-foreground">
          Both are read by the lead agent, so neither may hold a phone number, an email address, a link or an
          access code; a save with one is refused.
        </span>
      </p>

      <div className="grid gap-4 lg:grid-cols-2">
        <section
          aria-labelledby={id("facts-heading")}
          className="space-y-3 rounded-xl border-2 border-sky-300 bg-sky-50/60 p-4 dark:border-sky-800 dark:bg-sky-950/20"
        >
          <div className="flex items-center gap-2">
            <Globe className="h-4 w-4 text-sky-700 dark:text-sky-400" aria-hidden />
            <h3 id={id("facts-heading")} className="text-sm font-semibold">
              Public facts
            </h3>
            <Badge variant="outline" className="border-sky-400 text-sky-800 dark:text-sky-300">
              Told to leads
            </Badge>
          </div>
          <p className="text-xs text-muted-foreground">What we would print on a listing. One fact per line.</p>
          <LinesEditor
            id={id("fact")}
            lines={draft.public_facts}
            onChange={(lines) => set("public_facts", lines)}
            addLabel="Add public fact"
            placeholder="Dishwasher"
          />
        </section>
        <section
          aria-labelledby={id("notes-heading")}
          className="space-y-3 rounded-xl border-2 border-amber-300 bg-amber-50/60 p-4 dark:border-amber-800 dark:bg-amber-950/20"
        >
          <div className="flex items-center gap-2">
            <Lock className="h-4 w-4 text-amber-700 dark:text-amber-400" aria-hidden />
            <h3 id={id("notes-heading")} className="text-sm font-semibold">
              Internal notes
            </h3>
            <Badge variant="outline" className="border-amber-400 text-amber-800 dark:text-amber-300">
              Not told to leads
            </Badge>
          </div>
          <p className="text-xs text-muted-foreground">
            Helps decide, kept from leads. Low-stakes only: nothing that would matter if it slipped out.
          </p>
          <LinesEditor
            id={id("note")}
            lines={draft.internal_notes}
            onChange={(lines) => set("internal_notes", lines)}
            addLabel="Add internal note"
            placeholder="Resident wants 48 hours' notice"
          />
        </section>
      </div>

      <SaveBar
        dirty={dirty}
        saving={saving}
        onSave={save}
        refusal={refusal}
        justSaved={justSaved}
        onReload={onReload}
        onReset={() => {
          if (isNew) return onCancel();
          setDraft(original);
          setRefusal(null);
        }}
        saveLabel={isNew ? "Add unit" : "Save"}
        resetLabel={isNew ? "Cancel" : "Undo edits"}
      />

      {!isNew && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-base">
              <History className="h-4 w-4" aria-hidden />
              Changes to {original.unit}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <HistoryList changes={changes} loading={loading} />
          </CardContent>
        </Card>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Showing rules
// ---------------------------------------------------------------------------

const TIMEZONES = ["America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles"];

function RulesEditor({
  original,
  onSaved,
  onReload,
  onDirtyChange,
}: {
  original: ShowingRules;
  onSaved: (rules: ShowingRules) => void;
  onReload: () => void;
  onDirtyChange: (dirty: boolean) => void;
}) {
  const api = useApi();
  const [draft, setDraft] = useState<ShowingRules>(original);
  const [newBlackout, setNewBlackout] = useState("");
  const [saving, setSaving] = useState(false);
  const [refusal, setRefusal] = useState<{ refusal: Refusal; stale: boolean } | null>(null);
  const [justSaved, setJustSaved] = useState(false);
  const [historyKey, setHistoryKey] = useState(0);
  const { changes, loading } = useHistory(SHOWING_RULES_SUBJECT, historyKey);
  const today = localToday();

  const dirty = !sameRules(draft, original);
  useEffect(() => onDirtyChange(dirty), [dirty, onDirtyChange]);
  const set = <K extends keyof ShowingRules>(field: K, value: ShowingRules[K]) => {
    setDraft((rules) => ({ ...rules, [field]: value }));
    setJustSaved(false);
  };
  const addBlackout = () => {
    if (!newBlackout) return;
    set("blackout_dates", cleanRules({ ...draft, blackout_dates: [...draft.blackout_dates, newBlackout] }).blackout_dates);
    setNewBlackout("");
  };

  const save = async () => {
    setSaving(true);
    setRefusal(null);
    const result = await api<ShowingRules>("/showing-rules", {
      method: "PUT",
      body: JSON.stringify({ showing_rules: cleanRules(draft), based_on: original }),
    });
    setSaving(false);
    if (!result.ok) {
      setRefusal({ refusal: result.refusal, stale: result.status === 409 });
      return;
    }
    setDraft(result.body);
    setJustSaved(true);
    setHistoryKey((key) => key + 1);
    onSaved(result.body);
  };

  const blackouts = cleanRules(draft).blackout_dates;
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="pb-3">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div className="min-w-0">
              <CardTitle className="text-base">Showing rules</CardTitle>
              <CardDescription>Apply to every unit.</CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-6">
          <div className="grid gap-4 sm:grid-cols-3">
            <Field id="rules-earliest" label="Earliest showing start">
              <Input
                id="rules-earliest"
                type="time"
                value={shortTime(draft.earliest_start)}
                onChange={(event) => set("earliest_start", event.target.value)}
              />
            </Field>
            <Field id="rules-latest" label="Latest showing start">
              <Input
                id="rules-latest"
                type="time"
                value={shortTime(draft.latest_start)}
                onChange={(event) => set("latest_start", event.target.value)}
              />
            </Field>
            <Field id="rules-timezone" label="Time zone">
              <select
                id="rules-timezone"
                value={draft.timezone}
                onChange={(event) => set("timezone", event.target.value)}
                className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
              >
                {[...new Set([draft.timezone, ...TIMEZONES])].map((zone) => (
                  <option key={zone} value={zone}>
                    {zone}
                  </option>
                ))}
              </select>
            </Field>
          </div>

          <Field
            id="rules-booking"
            label="Shared booking link"
            hint="Used for any unit without its own booking link."
          >
            <Input
              id="rules-booking"
              type="url"
              inputMode="url"
              placeholder="https://"
              aria-describedby="rules-booking-hint"
              value={draft.showing_booking_url}
              onChange={(event) => set("showing_booking_url", event.target.value)}
            />
          </Field>

          <section aria-labelledby="rules-blackout-heading" className="space-y-2">
            <h3 id="rules-blackout-heading" className="text-sm font-semibold">
              Blackout dates
            </h3>
            <p className="text-xs text-muted-foreground">No showings at any unit on these days.</p>
            <div className="flex flex-wrap items-center gap-2">
              <Label htmlFor="rules-blackout-new" className="sr-only">
                Date to add
              </Label>
              <Input
                id="rules-blackout-new"
                type="date"
                className="w-44"
                min={today}
                value={newBlackout}
                onChange={(event) => setNewBlackout(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    event.preventDefault();
                    addBlackout();
                  }
                }}
              />
              <Button variant="outline" size="sm" className="gap-1.5" onClick={addBlackout} disabled={!newBlackout}>
                <Plus className="h-3.5 w-3.5" />
                Add date
              </Button>
            </div>
            {blackouts.length === 0 ? (
              <p className="text-sm text-muted-foreground">None.</p>
            ) : (
              <ul className="flex flex-wrap gap-2">
                {blackouts.map((day) => (
                  <li
                    key={day}
                    className={cn(
                      "inline-flex items-center gap-1 rounded-full border py-0.5 pl-3 pr-1 text-sm",
                      day < today && "text-muted-foreground line-through",
                    )}
                  >
                    {new Date(`${day}T12:00:00`).toLocaleDateString(undefined, {
                      weekday: "short",
                      month: "short",
                      day: "numeric",
                      year: "numeric",
                    })}
                    <button
                      type="button"
                      aria-label={`Remove blackout date ${day}`}
                      className="rounded-full p-1 hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      onClick={() => set("blackout_dates", draft.blackout_dates.filter((other) => other !== day))}
                    >
                      <X className="h-3 w-3" />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </CardContent>
      </Card>
      <SaveBar
        dirty={dirty}
        saving={saving}
        onSave={save}
        refusal={refusal}
        justSaved={justSaved}
        onReload={onReload}
        onReset={() => {
          setDraft(original);
          setRefusal(null);
        }}
      />
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2 text-base">
            <History className="h-4 w-4" aria-hidden />
            Changes to the showing rules
          </CardTitle>
        </CardHeader>
        <CardContent>
          <HistoryList changes={changes} loading={loading} />
        </CardContent>
      </Card>
    </div>
  );
}

function AllChanges() {
  const { changes, loading } = useHistory(null, 0);
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base">All changes</CardTitle>
        <CardDescription>
          The latest {HISTORY_LIMIT} saved changes to any unit or the showing rules, newest first.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <HistoryList changes={changes} loading={loading} showSubject />
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// The page
// ---------------------------------------------------------------------------

function MobileSidebarToggle() {
  const { toggleSidebar } = useSidebar();
  return (
    <Button variant="ghost" size="icon" className="h-8 w-8 shrink-0 md:hidden" onClick={toggleSidebar} aria-label="Open menu">
      <Menu className="h-4 w-4" />
    </Button>
  );
}

function unitStatus(unit: RentalListing, today: string): string {
  if (isOffMarket(unit, today)) return "Off market";
  if (unit.off_market_on) return `Off market from ${unit.off_market_on}`;
  return "On offer";
}

const LEAVE_WARNING = "You have edits that are not saved. Leave them?";

function RentalUnitsScreen() {
  const api = useApi();
  const [data, setData] = useState<RentalData | null>(null);
  const [loadRefusal, setLoadRefusal] = useState<Refusal | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [draftNew, setDraftNew] = useState<RentalListing | null>(null);
  const [newName, setNewName] = useState("");
  const [newNameProblem, setNewNameProblem] = useState<string | null>(null);
  const [tab, setTab] = useState("units");
  const [dirty, setDirty] = useState(false);
  // Bumped when what is on file is loaded again, so open editors start over from it.
  const [version, setVersion] = useState(0);
  const today = localToday();

  const load = useCallback(async () => {
    const result = await api<RentalData>("");
    if (!result.ok) {
      setLoadRefusal(result.refusal);
      return;
    }
    setData(result.body);
    setSelected((current) => current ?? result.body.listings[0]?.unit ?? null);
    setDirty(false);
    setVersion((value) => value + 1);
  }, [api]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const leaveEdits = () => !dirty || window.confirm(LEAVE_WARNING);
  const onDirtyChange = useCallback((value: boolean) => setDirty(value), []);

  const selectUnit = (name: string) => {
    if (name === selected && !draftNew) return;
    if (!leaveEdits()) return;
    setDraftNew(null);
    setSelected(name);
  };

  const startNew = () => {
    const name = newName.trim();
    if (!name) return;
    if (data?.listings.some((unit) => unit.unit.toLowerCase() === name.toLowerCase())) {
      setNewNameProblem(`Unit “${name}” already has a listing.`);
      return;
    }
    if (!leaveEdits()) return;
    setNewNameProblem(null);
    setDraftNew(newListing(name));
    setSelected(null);
    setNewName("");
  };

  const onUnitSaved = (saved: RentalListing) => {
    setData((current) => {
      if (!current) return current;
      const others = current.listings.filter((unit) => unit.unit.toLowerCase() !== saved.unit.toLowerCase());
      return { ...current, listings: [...others, saved].sort((a, b) => (a.unit < b.unit ? -1 : 1)) };
    });
    setDraftNew(null);
    setSelected(saved.unit);
  };

  const current = useMemo(
    () => data?.listings.find((unit) => unit.unit === selected) ?? null,
    [data, selected],
  );

  if (loadRefusal) {
    return (
      <div className="mx-auto max-w-2xl p-4">
        <RefusalBox refusal={loadRefusal} onReload={() => window.location.reload()} />
      </div>
    );
  }
  if (!data) {
    return (
      <div className="flex justify-center py-16">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" aria-label="Loading" />
      </div>
    );
  }

  return (
    <Tabs
      value={tab}
      onValueChange={(next) => {
        if (leaveEdits()) {
          setTab(next);
          setDirty(false);
        }
      }}
      className="mx-auto max-w-6xl space-y-4 p-4"
    >
      <TabsList>
        <TabsTrigger value="units">Units</TabsTrigger>
        <TabsTrigger value="rules">Showing rules</TabsTrigger>
        <TabsTrigger value="changes">All changes</TabsTrigger>
      </TabsList>

      <TabsContent value="units" className="mt-0">
        <div className="grid gap-4 md:grid-cols-[220px_minmax(0,1fr)]">
          <nav aria-label="Rental listings" className="space-y-3">
            <ul className="flex gap-2 overflow-x-auto pb-1 md:flex-col md:overflow-visible">
              {data.listings.map((unit) => {
                const active = unit.unit === selected && !draftNew;
                return (
                  <li key={unit.unit} className="shrink-0">
                    <button
                      type="button"
                      aria-current={active ? "true" : undefined}
                      onClick={() => selectUnit(unit.unit)}
                      className={cn(
                        "w-full rounded-md border px-3 py-2 text-left text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                        active ? "border-primary bg-primary/10" : "hover:bg-accent",
                      )}
                    >
                      <span className="block font-medium">{unit.unit}</span>
                      <span className="block text-xs text-muted-foreground">
                        {unitStatus(unit, today)}
                        {unit.asking_rent ? ` · $${unit.asking_rent.toLocaleString()}` : ""}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
            <form
              className="space-y-1.5"
              onSubmit={(event) => {
                event.preventDefault();
                startNew();
              }}
            >
              <Label htmlFor="new-unit-name">Add a unit</Label>
              <div className="flex gap-2">
                <Input
                  id="new-unit-name"
                  placeholder="Unit name"
                  maxLength={120}
                  value={newName}
                  aria-invalid={newNameProblem ? true : undefined}
                  aria-describedby={newNameProblem ? "new-unit-problem" : undefined}
                  onChange={(event) => {
                    setNewName(event.target.value);
                    setNewNameProblem(null);
                  }}
                />
                <Button type="submit" variant="outline" size="icon" className="shrink-0" aria-label="Start new unit" disabled={!newName.trim()}>
                  <Plus className="h-4 w-4" />
                </Button>
              </div>
              {newNameProblem && (
                <p id="new-unit-problem" className="text-xs text-red-600 dark:text-red-400">
                  {newNameProblem}
                </p>
              )}
              <p className="text-xs text-muted-foreground">A unit's name cannot be changed once added.</p>
            </form>
          </nav>

          <div className="min-w-0">
            {draftNew ? (
              <UnitEditor
                key={`new-${draftNew.unit}`}
                original={draftNew}
                isNew
                sharedBookingUrl={data.showing_rules.showing_booking_url}
                onSaved={onUnitSaved}
                onCancel={() => {
                  setDraftNew(null);
                  setDirty(false);
                  setSelected(data.listings[0]?.unit ?? null);
                }}
                onReload={load}
                onDirtyChange={onDirtyChange}
              />
            ) : current ? (
              <UnitEditor
                key={`${current.unit}-${version}`}
                original={current}
                isNew={false}
                sharedBookingUrl={data.showing_rules.showing_booking_url}
                onSaved={onUnitSaved}
                onCancel={() => {}}
                onReload={load}
                onDirtyChange={onDirtyChange}
              />
            ) : (
              <p className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">
                No units on file. Add one to start.
              </p>
            )}
          </div>
        </div>
      </TabsContent>

      <TabsContent value="rules" className="mt-0">
        <RulesEditor
          key={version}
          original={data.showing_rules}
          onReload={load}
          onSaved={(rules) => setData((current) => (current ? { ...current, showing_rules: rules } : current))}
          onDirtyChange={onDirtyChange}
        />
      </TabsContent>

      <TabsContent value="changes" className="mt-0">
        <AllChanges />
      </TabsContent>
    </Tabs>
  );
}

export default function SerniaRentalUnitsPage() {
  const navigate = useNavigate();
  return (
    <AuthGuard
      requireDomain="serniacapital.com"
      message="Sernia sign-in required"
      icon={<Building className="h-16 w-16 text-muted-foreground" />}
    >
      <SidebarProvider>
        <ConversationSidebar
          onSelectConversation={(id) => navigate(`/sernia-chat?id=${id}`)}
          onNewConversation={() => navigate("/sernia-chat")}
        />
        <SidebarInset className="min-w-0 overflow-x-hidden">
          <div className="flex h-chat-viewport flex-col bg-background">
            <div className="flex items-center gap-3 border-b px-4 py-3">
              <MobileSidebarToggle />
              <Button
                variant="ghost"
                size="icon"
                className="h-8 w-8 shrink-0"
                aria-label="Back to chat"
                onClick={() => navigate("/sernia-chat")}
              >
                <ArrowLeft className="h-4 w-4" />
              </Button>
              <div className="min-w-0 flex-1">
                <h1 className="text-lg font-semibold">Rental listings</h1>
                <p className="truncate text-xs text-muted-foreground">What the Zillow lead agent knows about each unit</p>
              </div>
            </div>
            <div className="flex-1 overflow-y-auto">
              <RentalUnitsScreen />
            </div>
          </div>
        </SidebarInset>
      </SidebarProvider>
    </AuthGuard>
  );
}
