import { useCallback, useEffect, useState } from "react";
import { useBlocker } from "react-router";
import { Loader2, Save } from "lucide-react";
import { Button } from "~/components/ui/button";
import { Input } from "~/components/ui/input";
import { Label } from "~/components/ui/label";
import { Switch } from "~/components/ui/switch";
import {
  type BookingSettings,
  type SettingsView,
  MODE_LABELS,
  SEND_MODES,
  describeMode,
  parseUnits,
  sameSettings,
  startsLiveSends,
} from "~/lib/showing-bookings";
import { cn } from "~/lib/utils";
import { useShowingBookingsApi } from "./use-showing-bookings-api";

function EnvironmentNote({ view }: { view: SettingsView }) {
  if (view.is_production) {
    return (
      <p className="mt-4 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm leading-6 text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200">
        This is production. In Live mode, messages for the live units go to real residents, agents and prospects.
      </p>
    );
  }
  const where = view.environment === "local" ? "a local copy of the app" : `the ${view.environment} environment`;
  return (
    <p className="mt-4 rounded-lg bg-muted p-3 text-sm leading-6">
      <strong className="font-medium">Outside production, everything is captured.</strong> This is {where}: messages are
      kept here and not sent, whatever the send mode says. Only a test send goes out, in Live mode with test sends on,
      to the test recipients below and no one else.
    </p>
  );
}

export function SendModeSection() {
  const api = useShowingBookingsApi();
  const [view, setView] = useState<SettingsView | null>(null);
  const [mode, setMode] = useState<BookingSettings["send_mode"]>("capture");
  const [unitsText, setUnitsText] = useState("");
  const [testSends, setTestSends] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  // Shown beside the Save button, where the person is looking. A stale save
  // (someone else saved first) also offers to load what is saved.
  const [saveError, setSaveError] = useState<{ message: string; stale: boolean } | null>(null);
  const [saved, setSaved] = useState(false);

  const show = useCallback((next: SettingsView) => {
    setView(next);
    setMode(next.settings.send_mode);
    setUnitsText(next.settings.live_units.join(", "));
    setTestSends(next.settings.test_sends_enabled);
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    setSaveError(null);
    setSaved(false);
    const result = await api<SettingsView>("/settings", {}, "Could not load the send mode");
    if (result.ok) show(result.body);
    else setLoadError(result.message);
    setLoading(false);
  }, [api, show]);

  useEffect(() => {
    void load();
  }, [load]);

  const form: BookingSettings = { send_mode: mode, live_units: parseUnits(unitsText), test_sends_enabled: testSends };
  const dirty = Boolean(view && !sameSettings(view.settings, form));

  const blocker = useBlocker(({ currentLocation, nextLocation }) => dirty && currentLocation.pathname !== nextLocation.pathname);
  useEffect(() => {
    if (blocker.state !== "blocked") return;
    if (window.confirm("You have an unsaved send mode. Leave this page?")) blocker.proceed();
    else blocker.reset();
  }, [blocker]);
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  async function save() {
    if (!view || !dirty || saving) return;
    if (
      startsLiveSends(view.settings, form, view.is_production) &&
      !window.confirm("This starts real texts and calendar invites in production. Save?")
    ) {
      return;
    }
    setSaving(true);
    setSaveError(null);
    setSaved(false);
    const result = await api<BookingSettings>(
      "/settings",
      { method: "PATCH", body: JSON.stringify({ settings: form, based_on: view.settings }) },
      "Could not save the send mode",
    );
    if (result.ok) {
      show({ ...view, settings: result.body });
      setSaved(true);
    } else {
      setSaveError({ message: result.message, stale: result.status === 409 });
    }
    setSaving(false);
  }

  return (
    <section aria-labelledby="send-mode-heading" className="rounded-xl border p-5 sm:p-6">
      <h3 id="send-mode-heading" className="text-xl font-semibold">Send mode</h3>
      <p className="mt-1 text-sm leading-6 text-muted-foreground">
        Whether the texts and calendar invites that bookings owe are sent, kept here without sending, or skipped.
      </p>
      {loading && !view && (
        <p role="status" className="mt-5 flex items-center gap-2 text-sm">
          <Loader2 className="size-4 animate-spin" aria-hidden="true" />
          Loading the send mode…
        </p>
      )}
      {view && <EnvironmentNote view={view} />}
      {loadError && (
        <div role="alert" className="mt-4 rounded-lg border border-destructive/50 p-3 text-sm">
          <p>{loadError}</p>
          <Button variant="outline" size="sm" className="mt-2" disabled={loading} onClick={() => void load()}>
            Try again
          </Button>
        </div>
      )}
      {view && (
        <form
          className="mt-5 space-y-6"
          onSubmit={(event) => {
            event.preventDefault();
            void save();
          }}
        >
          <fieldset disabled={saving} className="space-y-6">
            <fieldset className="grid gap-3 sm:grid-cols-3">
              <legend className="sr-only">Send mode</legend>
              {SEND_MODES.map((option) => (
                <label
                  key={option}
                  className={cn(
                    "flex cursor-pointer flex-col gap-1 rounded-lg border p-3 text-sm has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-ring",
                    mode === option ? "border-foreground bg-muted/50" : "hover:bg-muted/30",
                  )}
                >
                  <span className="flex items-center gap-2 font-medium">
                    <input
                      type="radio"
                      name="send-mode"
                      value={option}
                      checked={mode === option}
                      onChange={() => {
                        setMode(option);
                        setSaved(false);
                      }}
                      className="size-4 accent-foreground"
                    />
                    {MODE_LABELS[option]}
                  </span>
                  <span className="leading-5 text-muted-foreground">{describeMode(option, view)}</span>
                </label>
              ))}
            </fieldset>

            <div className="space-y-2">
              <Label htmlFor="live-units">Live units</Label>
              <Input
                id="live-units"
                value={unitsText}
                placeholder="None"
                autoComplete="off"
                aria-describedby="live-units-help"
                onChange={(event) => {
                  setUnitsText(event.target.value);
                  setSaved(false);
                }}
              />
              <p id="live-units-help" className="text-sm leading-6 text-muted-foreground">
                Units whose messages are sent in Live mode, in production only. Separate units with commas; each must be a
                rental listing. Turn units on one at a time.
              </p>
            </div>

            <div className="space-y-2">
              <div className="flex items-center justify-between gap-4">
                <Label htmlFor="test-sends">Test sends</Label>
                <Switch
                  id="test-sends"
                  checked={testSends}
                  aria-describedby="test-sends-help"
                  onCheckedChange={(checked) => {
                    setTestSends(checked);
                    setSaved(false);
                  }}
                />
              </div>
              <p id="test-sends-help" className="text-sm leading-6 text-muted-foreground">
                In Live mode, also send messages that reach only the test recipients, in any environment, so a text or an
                invite can be checked on a real phone.{" "}
                {view.test_recipients.length
                  ? `Test recipients here: ${view.test_recipients.join(", ")}.`
                  : "No test recipients are set in this environment (SHOWING_TEST_PHONES, SHOWING_TEST_EMAILS)."}
              </p>
            </div>
          </fieldset>

          <div className="flex flex-wrap items-center gap-3 border-t pt-5">
            <Button type="submit" disabled={!dirty || saving} className="gap-2">
              {saving ? <Loader2 className="size-4 animate-spin" aria-hidden="true" /> : <Save className="size-4" aria-hidden="true" />}
              Save send mode
            </Button>
            <Button
              type="button"
              variant="outline"
              disabled={!dirty || saving}
              onClick={() => {
                show(view);
                setSaveError(null);
              }}
            >
              Undo edits
            </Button>
            <p role="status" className="text-sm text-muted-foreground">
              {saved ? "Send mode saved." : dirty ? "Unsaved changes" : ""}
            </p>
          </div>
          {saveError && (
            <div role="alert" className="rounded-lg border border-destructive/50 p-3 text-sm">
              <p>{saveError.message}</p>
              {saveError.stale && (
                <Button variant="outline" size="sm" className="mt-2" disabled={loading} onClick={() => void load()}>
                  Load what is saved
                </Button>
              )}
            </div>
          )}
        </form>
      )}
    </section>
  );
}
