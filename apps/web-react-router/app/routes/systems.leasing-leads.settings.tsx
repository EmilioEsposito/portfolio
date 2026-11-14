import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@clerk/react-router";
import { Link, useBlocker } from "react-router";
import { Loader2, Save } from "lucide-react";
import { AuthGuard } from "~/components/auth-guard";
import { Button } from "~/components/ui/button";
import { Input } from "~/components/ui/input";
import { Label } from "~/components/ui/label";
import { Switch } from "~/components/ui/switch";

const endpoint = "/api/zillow-leads/settings";
type Settings = { debounce_seconds: number; require_approval: boolean };

export const meta = () => [
  { title: "Agent settings | Zillow Leads Agent" },
  { name: "robots", content: "noindex, nofollow" },
];

async function refusal(response: Response): Promise<string> {
  const body = await response.json().catch(() => null);
  return typeof body?.detail === "string" ? body.detail : `Could not save settings (${response.status}).`;
}

function LeadSettingsForm() {
  const { getToken } = useAuth();
  const [saved, setSaved] = useState<Settings | null>(null);
  const [form, setForm] = useState<Settings | null>(null);
  const [automationEnabled, setAutomationEnabled] = useState<boolean | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  const dirty = Boolean(saved && form && (saved.debounce_seconds !== form.debounce_seconds || saved.require_approval !== form.require_approval));
  const valid = Boolean(form && Number.isInteger(form.debounce_seconds) && form.debounce_seconds >= 60 && form.debounce_seconds <= 3600);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setSuccess(false);
    try {
      const token = await getToken();
      const response = await fetch(endpoint, { headers: { Authorization: `Bearer ${token}` } });
      if (!response.ok) throw new Error("Could not load settings. Try again.");
      const data = await response.json();
      const settings = { debounce_seconds: data.debounce_seconds, require_approval: data.require_approval };
      setSaved(settings);
      setForm(settings);
      setAutomationEnabled(data.automation_enabled);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load settings.");
    } finally {
      setLoading(false);
    }
  }, [getToken]);

  useEffect(() => { void load(); }, [load]);
  const blocker = useBlocker(({ currentLocation, nextLocation }) => dirty && currentLocation.pathname !== nextLocation.pathname);
  useEffect(() => {
    if (blocker.state !== "blocked") return;
    if (window.confirm("You have unsaved settings. Leave this page?")) blocker.proceed();
    else blocker.reset();
  }, [blocker]);
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  async function save() {
    if (!form || !saved || !valid || saving) return;
    setSaving(true);
    setError(null);
    setSuccess(false);
    try {
      const token = await getToken();
      const response = await fetch(endpoint, {
        method: "PATCH",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify({ settings: form, based_on: saved }),
      });
      if (!response.ok) throw new Error(await refusal(response));
      const settings: Settings = await response.json();
      setSaved(settings);
      setForm(settings);
      setSuccess(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save settings.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="max-w-2xl">
      <h2 className="text-2xl font-semibold">Agent settings</h2>
      <p className="mt-2 text-sm leading-6 text-muted-foreground">These settings apply to the Zillow Leads Agent only.</p>
      {loading && <p role="status" className="mt-6 flex items-center gap-2 text-sm"><Loader2 className="size-4 animate-spin" aria-hidden="true" />Loading settings…</p>}
      {error && <div role="alert" className="mt-5 rounded-lg border border-destructive/50 p-4 text-sm">
        <p>{error}</p>
        <Button variant="outline" size="sm" className="mt-3" disabled={loading || saving} onClick={() => void load()}>Reload saved settings</Button>
      </div>}
      {!loading && form && (
        <form className="mt-7 space-y-7" onSubmit={(event) => { event.preventDefault(); void save(); }}>
          <fieldset disabled={saving} className="space-y-7">
            <div className="rounded-xl border p-5">
              <div className="flex items-center justify-between gap-5">
                <Label htmlFor="lead-require-approval" className="text-base">Human approval for email replies</Label>
                <Switch id="lead-require-approval" checked={form.require_approval} onCheckedChange={(require_approval) => { setForm({ ...form, require_approval }); setSuccess(false); }} aria-describedby="approval-help" />
              </div>
              <p id="approval-help" className="mt-3 text-sm leading-6 text-muted-foreground">
                {form.require_approval
                  ? "Every proposed reply waits for the team to review and approve it."
                  : "New eligible replies are sent automatically. Exceptions still go to the team; existing drafts remain pending."}
              </p>
            </div>
            <div className="space-y-2">
              <Label htmlFor="lead-debounce">Wait before processing a thread (seconds)</Label>
              <p id="debounce-help" className="text-sm leading-6 text-muted-foreground">Group messages arriving close together into one response. Choose 60–3,600 seconds.</p>
              <Input id="lead-debounce" type="number" min={60} max={3600} step={1} required className="max-w-40" value={form.debounce_seconds} aria-describedby="debounce-help" aria-invalid={!valid} onChange={(event) => { setForm({ ...form, debounce_seconds: Number(event.target.value) }); setSuccess(false); }} />
              {!valid && <p className="text-sm text-destructive">Enter a whole number from 60 to 3,600.</p>}
            </div>
          </fieldset>
          {automationEnabled === false && <p className="rounded-lg bg-muted p-3 text-sm leading-6 text-muted-foreground">Automated runs are currently disabled. Saving these settings does not enable the <Link to="/sernia-settings" className="text-foreground underline underline-offset-4">shared automation switch</Link>.</p>}
          <div className="flex flex-wrap items-center gap-3 border-t pt-5">
            <Button type="submit" disabled={!dirty || !valid || saving} className="gap-2">{saving ? <Loader2 className="size-4 animate-spin" aria-hidden="true" /> : <Save className="size-4" aria-hidden="true" />}Save settings</Button>
            <Button type="button" variant="outline" disabled={!dirty || saving} onClick={() => { setForm(saved); setError(null); setSuccess(false); }}>Undo edits</Button>
            <p role="status" className="text-sm text-muted-foreground">{success ? "Settings saved." : dirty ? "Unsaved changes" : ""}</p>
          </div>
        </form>
      )}
      <Link to="/sernia-chat" className="mt-7 inline-block text-sm underline underline-offset-4">Open agent conversations</Link>
    </section>
  );
}

export default function ZillowLeadSettingsPage() {
  return <AuthGuard requireDomain="serniacapital.com" message="Sernia sign-in required"><LeadSettingsForm /></AuthGuard>;
}
