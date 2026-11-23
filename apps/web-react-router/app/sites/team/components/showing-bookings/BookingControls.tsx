import { useCallback, useEffect, useState } from "react";
import { Link, useBlocker } from "react-router";
import { CircleAlert, Loader2, Save } from "lucide-react";
import { Button } from "~/components/ui/button";
import {
  type BookingSettings,
  type SendMode,
  type SettingsView,
  MODE_DESCRIPTIONS,
  MODE_TITLES,
  SEND_MODES,
  afterSaving,
  isTicked,
  liveStartQuestion,
  rightNow,
  sameSettings,
  tickUnit,
  unitTicks,
  untickedNote,
} from "~/sites/team/lib/showing-bookings";
import { cn } from "~/lib/utils";
import { useShowingBookingsApi } from "./use-showing-bookings-api";

const NOTE =
  "flex items-start gap-2 rounded-md bg-amber-50 p-2.5 text-sm leading-5 text-amber-900 dark:bg-amber-950/30 dark:text-amber-200";

function AmberNote({ children }: { children: React.ReactNode }) {
  return (
    <p className={cn(NOTE, "mt-2")}>
      <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
      <span>{children}</span>
    </p>
  );
}

// ---------------------------------------------------------------------------
// Who can book. Each change is saved at once.
// ---------------------------------------------------------------------------

function WhoRow({
  id,
  label,
  help,
  checked,
  disabled = false,
  saving = false,
  error = null,
  onChange,
  children,
}: {
  id: string;
  label: string;
  help: string;
  checked: boolean;
  disabled?: boolean;
  saving?: boolean;
  error?: string | null;
  onChange?: (checked: boolean) => void;
  children?: React.ReactNode;
}) {
  return (
    <li>
      <label
        htmlFor={id}
        className={cn(
          "flex items-start gap-3 rounded-lg border p-3 text-sm has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-ring",
          disabled ? "bg-muted/40" : "cursor-pointer hover:bg-muted/30",
        )}
      >
        <input
          id={id}
          type="checkbox"
          checked={checked}
          disabled={disabled}
          aria-busy={saving}
          // Not disabled while saving: that would drop the focus.
          onChange={(event) => {
            if (!saving) onChange?.(event.target.checked);
          }}
          className="mt-0.5 size-4 shrink-0 accent-foreground"
        />
        <span className="min-w-0 flex-1">
          <span className="font-medium">{label}</span> <span className="text-muted-foreground">{help}</span>
        </span>
        {saving && <Loader2 className="mt-0.5 size-4 shrink-0 animate-spin" aria-label="Saving" />}
      </label>
      {children}
      {error && (
        <p role="alert" className="mt-1.5 text-sm text-destructive">
          {error}
        </p>
      )}
    </li>
  );
}

function WhoCanBook({
  view,
  pagesOn,
  onChanged,
}: {
  view: SettingsView;
  pagesOn: boolean;
  onChanged: (next: SettingsView) => void;
}) {
  const api = useShowingBookingsApi();
  const [saving, setSaving] = useState<"public" | "leads" | null>(null);
  const [errors, setErrors] = useState<{ public?: string; leads?: string }>({});

  async function setPublic(on: boolean) {
    if (
      on &&
      view.is_production &&
      !window.confirm("This lets anyone book a showing on the public booking page. Turn it on?")
    ) {
      return;
    }
    setSaving("public");
    setErrors((current) => ({ ...current, public: undefined }));
    const result = await api<SettingsView>(
      "/settings/public-booking",
      { method: "PUT", body: JSON.stringify({ public_booking_enabled: on }) },
      "Could not change public booking",
    );
    if (result.ok) onChanged(result.body);
    else setErrors((current) => ({ ...current, public: result.message }));
    setSaving(null);
  }

  async function setLeads(on: boolean) {
    const madeUp = view.is_production ? "" : "Some open times here may come from a made-up calendar. ";
    if (
      on &&
      !window.confirm(
        `${madeUp}The Zillow lead agent will offer open times to leads, and replies you approve will book, move and cancel showings. Turn it on?`,
      )
    ) {
      return;
    }
    setSaving("leads");
    setErrors((current) => ({ ...current, leads: undefined }));
    const result = await api<BookingSettings>(
      "/settings/lead-agent",
      { method: "PUT", body: JSON.stringify({ lead_agent_uses_open_times: on }) },
      "Could not change what the lead agent offers",
    );
    if (result.ok) onChanged({ ...view, settings: result.body });
    else setErrors((current) => ({ ...current, leads: result.message }));
    setSaving(null);
  }

  const leadsOn = view.settings.lead_agent_uses_open_times;
  return (
    <section aria-labelledby="who-can-book-heading" className="rounded-xl border p-5 sm:p-6">
      <h3 id="who-can-book-heading" className="text-xl font-semibold">
        Who can book
      </h3>
      <p className="mt-1 text-sm leading-6 text-muted-foreground">
        Turning one off leaves existing showings as they are. Each change here is saved at once.
      </p>
      <ul className="mt-4 space-y-2.5">
        <WhoRow id="book-team" label="Your team, on this page." help="Always on." checked disabled />
        <WhoRow
          id="book-public"
          label="Anyone, on the public booking page."
          help="Off: the page says nothing is open."
          checked={view.public_booking_on}
          saving={saving === "public"}
          error={errors.public}
          onChange={(on) => void setPublic(on)}
        >
          {view.public_booking_on && !pagesOn && (
            <AmberNote>
              The booking pages are off in this environment (SERNIA_PUBLIC_PAGES_ENABLED), so they answer 404.
            </AmberNote>
          )}
        </WhoRow>
        <WhoRow
          id="book-leads"
          label="Zillow leads, through the lead agent."
          help="Its replies offer open times, and a reply you approve books the showing."
          checked={leadsOn}
          saving={saving === "leads"}
          error={errors.leads}
          onChange={(on) => void setLeads(on)}
        >
          {leadsOn && !pagesOn && (
            <AmberNote>The links in its replies open the booking pages, which are off in this environment.</AmberNote>
          )}
        </WhoRow>
      </ul>
    </section>
  );
}

// ---------------------------------------------------------------------------
// What a booking sends. Edits are saved together.
// ---------------------------------------------------------------------------

/** A mode's own settings, under it: disabled and grayed while another mode is chosen. */
function ModeSettings({ mode, chosen, legend, children }: {
  mode: SendMode;
  chosen: boolean;
  legend: string;
  children: React.ReactNode;
}) {
  return (
    <fieldset
      disabled={!chosen}
      aria-disabled={!chosen}
      className={cn("ml-7 mt-2 min-w-0 border-l pl-4 text-sm", !chosen && "opacity-50")}
    >
      <legend className="sr-only">{chosen ? legend : `${legend}. Only used in ${MODE_TITLES[mode]}.`}</legend>
      {children}
    </fieldset>
  );
}

function TestList({ recipients }: { recipients: string[] }) {
  if (recipients.length === 0) {
    return (
      <p className="py-1 leading-6">
        No test list is set here (SHOWING_TEST_PHONES and SHOWING_TEST_EMAILS), so everything is captured.
      </p>
    );
  }
  return (
    <p className="py-1 leading-6">
      <span className="font-medium">Test list:</span> <span className="break-words">{recipients.join(", ")}</span>
    </p>
  );
}

function LiveUnits({
  view,
  form,
  onChange,
}: {
  view: SettingsView;
  form: BookingSettings;
  onChange: (changes: Partial<BookingSettings>) => void;
}) {
  // A ticked unit whose listing is gone stays here until it is saved without it.
  const units = unitTicks(view.listings, view.settings.live_units, form.live_units);
  return (
    <div className="space-y-2 py-1">
      <label className="flex items-start gap-2.5">
        <input
          type="checkbox"
          checked={form.live_all_units}
          onChange={(event) => onChange({ live_all_units: event.target.checked })}
          className="mt-0.5 size-4 shrink-0 accent-foreground"
        />
        <span className="font-medium">All units, including ones added later</span>
      </label>
      {units.length === 0 ? (
        <p className="leading-6">
          There are no rental listings yet. Add them under{" "}
          <Link to="/systems/leasing-leads/listings" className="underline underline-offset-4">
            Rental listing data
          </Link>
          .
        </p>
      ) : (
        <fieldset disabled={form.live_all_units} className={cn("min-w-0", form.live_all_units && "opacity-50")}>
          <legend className="sr-only">
            {form.live_all_units ? "Units. All units is on, so each one is live." : "Units that are live"}
          </legend>
          <ul className="grid gap-x-6 gap-y-1.5 sm:grid-cols-2">
            {units.map((unit) => (
              <li key={unit.unit}>
                <label className="flex items-start gap-2.5">
                  <input
                    type="checkbox"
                    checked={isTicked(form.live_units, unit.unit)}
                    onChange={(event) =>
                      onChange({ live_units: tickUnit(form.live_units, unit.unit, event.target.checked) })
                    }
                    className="mt-0.5 size-4 shrink-0 accent-foreground"
                  />
                  <span className="min-w-0 break-words">
                    {unit.unit}
                    <span className="text-muted-foreground">
                      {unit.listed ? unit.address && ` · ${unit.address}` : " · no rental listing any more"}
                    </span>
                  </span>
                </label>
              </li>
            ))}
          </ul>
        </fieldset>
      )}
      {!form.live_all_units && (
        <p className="leading-6 text-muted-foreground">Unticked units can&rsquo;t be booked by the public or by leads.</p>
      )}
    </div>
  );
}

function WhatABookingSends({
  view,
  form,
  dirty,
  saving,
  saved,
  saveError,
  reloading,
  onChange,
  onSave,
  onUndo,
  onReload,
}: {
  view: SettingsView;
  form: BookingSettings;
  dirty: boolean;
  saving: boolean;
  saved: boolean;
  saveError: { message: string; stale: boolean } | null;
  reloading: boolean;
  onChange: (changes: Partial<BookingSettings>) => void;
  onSave: () => void;
  onUndo: () => void;
  onReload: () => void;
}) {
  const unticked = untickedNote(view.settings, form, view.listings);
  const where = view.environment === "local" ? "a local copy of the app" : `the ${view.environment} environment`;
  const nested: Record<SendMode, React.ReactNode> = {
    test: (
      <ModeSettings mode="test" chosen={form.send_mode === "test"} legend="Test list">
        <TestList recipients={view.test_recipients} />
      </ModeSettings>
    ),
    capture: null,
    live: (
      <ModeSettings mode="live" chosen={form.send_mode === "live"} legend="Live units">
        <LiveUnits view={view} form={form} onChange={onChange} />
      </ModeSettings>
    ),
  };
  return (
    <section aria-labelledby="what-sends-heading" className="rounded-xl border p-5 sm:p-6">
      <h3 id="what-sends-heading" className="text-xl font-semibold">
        What a booking sends
      </h3>
      <p id="what-sends-help" className="mt-1 text-sm leading-6 text-muted-foreground">
        Texts and calendar invites for the prospect, residents and host. The booking itself is always real.
      </p>
      {!view.is_production && (
        <p className="mt-4 rounded-lg bg-muted p-3 text-sm leading-6">
          <strong className="font-medium">Outside production only the test list can be reached.</strong> This is {where}:
          whatever is chosen here, a message to anyone else is captured.
        </p>
      )}
      <form
        className="mt-4"
        onSubmit={(event) => {
          event.preventDefault();
          onSave();
        }}
      >
        <fieldset disabled={saving} aria-describedby="what-sends-help" className="min-w-0 space-y-4">
          <legend className="sr-only">What a booking sends</legend>
          {SEND_MODES.map((mode) => (
            <div key={mode}>
              <label className="flex cursor-pointer items-start gap-3 rounded-md text-sm has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-ring">
                <input
                  type="radio"
                  name="send-mode"
                  value={mode}
                  checked={form.send_mode === mode}
                  onChange={() => onChange({ send_mode: mode })}
                  className="mt-0.5 size-4 shrink-0 accent-foreground"
                />
                <span className="min-w-0">
                  <span className="font-medium">{MODE_TITLES[mode]}.</span>{" "}
                  <span className="text-muted-foreground">{MODE_DESCRIPTIONS[mode]}</span>
                </span>
              </label>
              {nested[mode]}
            </div>
          ))}
        </fieldset>
        <p role="status" aria-live="polite">
          {unticked && (
            <span className={cn(NOTE, "mt-4")}>
              <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
              {unticked}
            </span>
          )}
        </p>
        <div className="mt-5 flex flex-wrap items-center gap-3 border-t pt-5">
          <Button type="submit" disabled={!dirty || saving} className="gap-2">
            {saving ? (
              <Loader2 className="size-4 animate-spin" aria-hidden="true" />
            ) : (
              <Save className="size-4" aria-hidden="true" />
            )}
            Save
          </Button>
          <Button type="button" variant="outline" disabled={!dirty || saving} onClick={onUndo}>
            Undo edits
          </Button>
          <p role="status" className="text-sm text-muted-foreground">
            {saved ? "Saved." : dirty ? "Unsaved changes" : ""}
          </p>
        </div>
        {saveError && (
          <div role="alert" className="mt-4 rounded-lg border border-destructive/50 p-3 text-sm">
            <p>{saveError.message}</p>
            {saveError.stale && (
              <Button variant="outline" size="sm" type="button" className="mt-2" disabled={reloading} onClick={onReload}>
                Load what is saved
              </Button>
            )}
          </div>
        )}
      </form>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Both, with what they mean right now
// ---------------------------------------------------------------------------

/** The settings at the top of the tab. The page keeps the saved ones, which other sections read. */
export function BookingControls({
  pagesOn,
  view,
  onView,
}: {
  /** The web app serves the public booking pages in this environment. */
  pagesOn: boolean;
  view: SettingsView | null;
  onView: (view: SettingsView) => void;
}) {
  const api = useShowingBookingsApi();
  // The unsaved edits: the send mode and the live units, nothing else.
  const [edits, setEdits] = useState<Pick<BookingSettings, "send_mode" | "live_units" | "live_all_units"> | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  // Shown beside the Save button, where the person is looking. A stale save
  // (someone else saved first) also offers to load what is saved.
  const [saveError, setSaveError] = useState<{ message: string; stale: boolean } | null>(null);
  const [saved, setSaved] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    setSaveError(null);
    setSaved(false);
    const result = await api<SettingsView>("/settings", {}, "Could not load the settings");
    if (result.ok) {
      onView(result.body);
      setEdits(null);
    } else {
      setLoadError(result.message);
    }
    setLoading(false);
  }, [api, onView]);

  useEffect(() => {
    void load();
  }, [load]);

  const form: BookingSettings | null = view && { ...view.settings, ...edits };
  const dirty = Boolean(view && form && !sameSettings(view.settings, form));

  const blocker = useBlocker(({ currentLocation, nextLocation }) => dirty && currentLocation.pathname !== nextLocation.pathname);
  useEffect(() => {
    if (blocker.state !== "blocked") return;
    if (window.confirm("You have unsaved changes to what a booking sends. Leave this page?")) blocker.proceed();
    else blocker.reset();
  }, [blocker]);
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  async function save() {
    if (!view || !form || !dirty || saving) return;
    const question = liveStartQuestion(view.settings, form, view.is_production);
    if (question && !window.confirm(question)) return;
    setSaving(true);
    setSaveError(null);
    setSaved(false);
    const result = await api<BookingSettings>(
      "/settings",
      { method: "PATCH", body: JSON.stringify({ settings: form, based_on: view.settings }) },
      "Could not save",
    );
    if (result.ok) {
      onView({ ...view, settings: result.body });
      setEdits(null);
      setSaved(true);
    } else {
      setSaveError({ message: result.message, stale: result.status === 409 });
    }
    setSaving(false);
  }

  if (!view || !form) {
    return (
      <section aria-label="Booking settings" className="rounded-xl border p-5 sm:p-6">
        {loading ? (
          <p role="status" className="flex items-center gap-2 text-sm">
            <Loader2 className="size-4 animate-spin" aria-hidden="true" />
            Loading the settings…
          </p>
        ) : (
          <div role="alert" className="rounded-lg border border-destructive/50 p-3 text-sm">
            <p>{loadError}</p>
            <Button variant="outline" size="sm" className="mt-2" onClick={() => void load()}>
              Try again
            </Button>
          </div>
        )}
      </section>
    );
  }
  return (
    <>
      <div role="status" aria-live="polite" className="rounded-xl border bg-muted/50 p-4 text-sm leading-6 sm:px-6">
        <p>
          <strong className="font-semibold">Right now:</strong> {rightNow(view, pagesOn)}
        </p>
        {dirty && (
          <p className="mt-1">
            <strong className="font-semibold">After saving:</strong> {afterSaving(view, form, pagesOn)}
          </p>
        )}
      </div>
      <WhoCanBook view={view} pagesOn={pagesOn} onChanged={onView} />
      <WhatABookingSends
        view={view}
        form={form}
        dirty={dirty}
        saving={saving}
        saved={saved && !dirty}
        saveError={saveError}
        reloading={loading}
        onChange={(changes) => {
          setEdits({
            send_mode: form.send_mode,
            live_units: form.live_units,
            live_all_units: form.live_all_units,
            ...changes,
          });
          setSaved(false);
        }}
        onSave={() => void save()}
        onUndo={() => {
          setEdits(null);
          setSaveError(null);
        }}
        onReload={() => void load()}
      />
      {loadError && (
        <p role="alert" className="rounded-lg border border-destructive/50 p-3 text-sm">
          {loadError}
        </p>
      )}
    </>
  );
}
