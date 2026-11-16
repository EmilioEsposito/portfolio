import { useCallback, useEffect, useState } from "react";
import { Loader2, Pencil, Plus, Power, Save, UsersRound } from "lucide-react";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Input } from "~/components/ui/input";
import { Label } from "~/components/ui/label";
import { Textarea } from "~/components/ui/textarea";
import {
  type AgentView,
  type ConnectionsView,
  type SettingsView,
  type ShowingAgent,
  cleanAgent,
  describeWindows,
  newAgent,
  parseCalendars,
} from "~/lib/showing-bookings";
import { cn } from "~/lib/utils";
import { useShowingBookingsApi } from "./use-showing-bookings-api";
import { WindowsEditor } from "./WindowsEditor";

type Editing = { id: number | "new"; draft: ShowingAgent; calendarsText: string } | null;

function AgentForm({
  editing,
  connections,
  saving,
  problems,
  onChange,
  onSave,
  onCancel,
}: {
  editing: NonNullable<Editing>;
  connections: ConnectionsView | null;
  saving: boolean;
  problems: string[];
  onChange: (next: NonNullable<Editing>) => void;
  onSave: () => void;
  onCancel: () => void;
}) {
  const { draft } = editing;
  const set = <K extends keyof ShowingAgent>(key: K, value: ShowingAgent[K]) =>
    onChange({ ...editing, draft: { ...draft, [key]: value } });
  const prefix = `agent-${editing.id}`;
  const usable = (connections?.connections ?? []).filter((row) => row.status !== "revoked");
  return (
    <form
      className="space-y-5 rounded-lg border p-4"
      aria-label={editing.id === "new" ? "New showing agent" : `Change ${draft.name}`}
      onSubmit={(event) => {
        event.preventDefault();
        onSave();
      }}
    >
      <fieldset disabled={saving} className="space-y-5">
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor={`${prefix}-name`}>Name</Label>
            <Input
              id={`${prefix}-name`}
              value={draft.name}
              maxLength={120}
              required
              autoComplete="off"
              onChange={(event) => set("name", event.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={`${prefix}-email`}>Email</Label>
            <Input
              id={`${prefix}-email`}
              type="email"
              value={draft.email}
              required
              autoComplete="off"
              aria-describedby={`${prefix}-email-hint`}
              onChange={(event) => set("email", event.target.value)}
            />
            <p id={`${prefix}-email-hint`} className="text-xs text-muted-foreground">
              Calendar invites for their showings go here.
            </p>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={`${prefix}-mobile`}>Mobile</Label>
            <Input
              id={`${prefix}-mobile`}
              type="tel"
              inputMode="tel"
              value={draft.mobile ?? ""}
              autoComplete="off"
              aria-describedby={`${prefix}-mobile-hint`}
              onChange={(event) => set("mobile", event.target.value)}
            />
            <p id={`${prefix}-mobile-hint`} className="text-xs text-muted-foreground">
              A ten-digit US mobile, for showing texts. Optional.
            </p>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={`${prefix}-priority`}>Priority</Label>
            <Input
              id={`${prefix}-priority`}
              type="number"
              inputMode="numeric"
              min={1}
              max={99}
              step={1}
              value={Number.isFinite(draft.priority) ? draft.priority : ""}
              aria-describedby={`${prefix}-priority-hint`}
              onChange={(event) => set("priority", Math.round(Number(event.target.value)))}
            />
            <p id={`${prefix}-priority-hint`} className="text-xs text-muted-foreground">
              When several agents are free, 1 hosts before 2.
            </p>
          </div>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor={`${prefix}-calendars`}>Calendars that make them busy</Label>
          <Textarea
            id={`${prefix}-calendars`}
            rows={2}
            value={editing.calendarsText}
            placeholder="jane.doe@example.com"
            aria-describedby={`${prefix}-calendars-hint`}
            onChange={(event) => onChange({ ...editing, calendarsText: event.target.value })}
          />
          <p id={`${prefix}-calendars-hint`} className="text-xs text-muted-foreground">
            One calendar address per line. A personal calendar must be shared with the connected account as “See only
            free/busy”. With none listed, the agent counts as busy.
          </p>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor={`${prefix}-connection`}>Read through</Label>
          <select
            id={`${prefix}-connection`}
            value={draft.connection_id ?? ""}
            onChange={(event) => set("connection_id", event.target.value ? Number(event.target.value) : null)}
            className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
          >
            <option value="">The team&rsquo;s connection</option>
            {usable.map((row) => (
              <option key={row.id} value={row.id}>
                {row.google_email}
              </option>
            ))}
          </select>
        </div>

        <section aria-labelledby={`${prefix}-hours-heading`} className="space-y-2">
          <h4 id={`${prefix}-hours-heading`} className="text-sm font-semibold">
            Hours
          </h4>
          <p className="text-xs text-muted-foreground">
            When this agent can show, as the first and last start times. Showings still stay inside the showing window.
          </p>
          <WindowsEditor
            id={`${prefix}-hours`}
            noun="hours"
            empty="No hours set: any time in the showing window."
            windows={draft.weekly_hours}
            onChange={(windows) => set("weekly_hours", windows)}
          />
        </section>

        <section aria-labelledby={`${prefix}-preferred-heading`} className="space-y-2">
          <h4 id={`${prefix}-preferred-heading`} className="text-sm font-semibold">
            Preferred times
          </h4>
          <p className="text-xs text-muted-foreground">
            Times this agent would rather show. They are offered before other times, and the agent hosts them first.
          </p>
          <WindowsEditor
            id={`${prefix}-preferred`}
            noun="preferred time"
            empty="No preference."
            windows={draft.preferred_windows}
            onChange={(windows) => set("preferred_windows", windows)}
          />
        </section>
      </fieldset>

      {problems.length > 0 && (
        <div role="alert" className="rounded-lg border border-destructive/50 p-3 text-sm">
          <p className="font-medium">Not saved.</p>
          <ul className="mt-1 list-disc pl-5">
            {problems.map((problem) => (
              <li key={problem}>{problem}</li>
            ))}
          </ul>
        </div>
      )}
      <div className="flex flex-wrap gap-3 border-t pt-4">
        <Button type="submit" disabled={saving} className="gap-2">
          {saving ? <Loader2 className="size-4 animate-spin" aria-hidden="true" /> : <Save className="size-4" aria-hidden="true" />}
          {editing.id === "new" ? "Add agent" : "Save agent"}
        </Button>
        <Button type="button" variant="outline" disabled={saving} onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

function AgentItem({
  agent,
  busy,
  onEdit,
  onToggle,
}: {
  agent: AgentView;
  busy: boolean;
  onEdit: () => void;
  onToggle: () => void;
}) {
  return (
    <li className={cn("rounded-lg border p-4", !agent.active && "bg-muted/40")}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="font-medium">
            {agent.name} <span className="text-sm font-normal text-muted-foreground">· priority {agent.priority}</span>
          </p>
          <p className="break-all text-sm text-muted-foreground">
            {agent.email}
            {agent.mobile ? ` · ${agent.mobile}` : ""}
          </p>
        </div>
        <Badge
          variant="outline"
          className={cn(
            "shrink-0 font-medium",
            agent.active ? "border-green-300 text-green-800 dark:border-green-900 dark:text-green-400" : "text-muted-foreground",
          )}
        >
          {agent.active ? "Active" : "Inactive"}
        </Badge>
      </div>
      <dl className="mt-3 grid gap-x-4 gap-y-1 text-sm sm:grid-cols-[auto_1fr]">
        <dt className="text-muted-foreground">Hours</dt>
        <dd>{describeWindows(agent.weekly_hours, "Any time in the showing window")}</dd>
        <dt className="text-muted-foreground">Prefers</dt>
        <dd>{describeWindows(agent.preferred_windows, "No preference")}</dd>
        <dt className="text-muted-foreground">Busy when</dt>
        <dd className="break-all">
          {agent.busy_calendar_ids.length ? agent.busy_calendar_ids.join(", ") : "No calendars listed: counts as busy"}
        </dd>
        <dt className="text-muted-foreground">Read through</dt>
        <dd className="break-all">
          {agent.reads_through ?? "No calendar connected"}
          {agent.connection_id === null && agent.reads_through ? " (the team's)" : ""}
        </dd>
      </dl>
      <div className="mt-3 flex flex-wrap gap-2">
        <Button variant="outline" size="sm" className="gap-1.5" disabled={busy} onClick={onEdit}>
          <Pencil className="size-3.5" aria-hidden="true" />
          Change
        </Button>
        <Button variant="outline" size="sm" className="gap-1.5" disabled={busy} onClick={onToggle}>
          {busy ? <Loader2 className="size-3.5 animate-spin" aria-hidden="true" /> : <Power className="size-3.5" aria-hidden="true" />}
          {agent.active ? "Deactivate" : "Make active"}
        </Button>
      </div>
    </li>
  );
}

export function AgentsSection() {
  const api = useShowingBookingsApi();
  const [agents, setAgents] = useState<AgentView[] | null>(null);
  const [connections, setConnections] = useState<ConnectionsView | null>(null);
  const [isProduction, setIsProduction] = useState(true);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [editing, setEditing] = useState<Editing>(null);
  const [saving, setSaving] = useState(false);
  const [problems, setProblems] = useState<string[]>([]);
  const [toggling, setToggling] = useState<number | null>(null);
  const [notice, setNotice] = useState<{ ok: boolean; message: string } | null>(null);
  const [loadingSamples, setLoadingSamples] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    const [list, rows, settings] = await Promise.all([
      api<AgentView[]>("/agents", {}, "Could not load the showing agents"),
      api<ConnectionsView>("/calendar/connections", {}, "Could not load the calendar connections"),
      api<SettingsView>("/settings", {}, "Could not load the environment"),
    ]);
    if (list.ok) setAgents(list.body);
    else setLoadError(list.message);
    if (rows.ok) setConnections(rows.body);
    if (settings.ok) setIsProduction(settings.body.is_production);
    setLoading(false);
  }, [api]);

  useEffect(() => {
    void load();
  }, [load]);

  function edit(agent: AgentView | null) {
    setProblems([]);
    setNotice(null);
    if (agent === null) {
      setEditing({ id: "new", draft: newAgent(), calendarsText: "" });
      return;
    }
    const { id, reads_through: _readsThrough, ...draft } = agent;
    setEditing({ id, draft, calendarsText: draft.busy_calendar_ids.join("\n") });
  }

  async function save() {
    if (!editing || saving) return;
    setSaving(true);
    setProblems([]);
    const body = cleanAgent({ ...editing.draft, busy_calendar_ids: parseCalendars(editing.calendarsText) });
    const path = editing.id === "new" ? "/agents" : `/agents/${editing.id}`;
    const result = await rawSave(path, editing.id === "new" ? "POST" : "PUT", body);
    if (result.ok) {
      setEditing(null);
      setNotice({ ok: true, message: `${result.body.name} saved.` });
      await load();
    } else {
      setProblems(result.problems);
    }
    setSaving(false);
  }

  async function rawSave(
    path: string,
    method: string,
    body: ShowingAgent,
  ): Promise<{ ok: true; body: AgentView } | { ok: false; problems: string[] }> {
    const result = await api<AgentView>(path, { method, body: JSON.stringify(body) }, "Could not save the agent");
    if (result.ok) return result;
    return { ok: false, problems: result.problems.length ? result.problems : [result.message] };
  }

  async function toggle(agent: AgentView) {
    if (agent.active && !window.confirm(`Deactivate ${agent.name}? They get no new showings until made active again.`)) return;
    setToggling(agent.id);
    setNotice(null);
    const result = await api<AgentView>(
      `/agents/${agent.id}/${agent.active ? "deactivate" : "activate"}`,
      { method: "POST" },
      "Could not change the agent",
    );
    if (result.ok) {
      setAgents((list) => (list ?? []).map((item) => (item.id === agent.id ? result.body : item)));
    } else {
      setNotice({ ok: false, message: result.message });
    }
    setToggling(null);
  }

  async function loadSamples() {
    setLoadingSamples(true);
    setNotice(null);
    const result = await api<{ added: string[] }>("/agents/load-samples", { method: "POST" }, "Could not add sample agents");
    if (result.ok) {
      setNotice({
        ok: true,
        message: result.body.added.length
          ? `Added ${result.body.added.join(" and ")}, made up for trying the tab.`
          : "The sample agents are already here.",
      });
      await load();
    } else {
      setNotice({ ok: false, message: result.message });
    }
    setLoadingSamples(false);
  }

  return (
    <section aria-labelledby="agents-heading" className="rounded-xl border p-5 sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 id="agents-heading" className="text-xl font-semibold">
            Showing agents
          </h3>
          <p className="mt-1 text-sm leading-6 text-muted-foreground">
            Who can host showings: their hours, preferred times, priority, and the calendars that make them busy. An
            inactive agent gets no new showings.
          </p>
        </div>
        {!editing && agents && (
          <Button size="sm" className="gap-1.5" onClick={() => edit(null)}>
            <Plus className="size-4" aria-hidden="true" />
            Add agent
          </Button>
        )}
      </div>

      {loading && !agents && (
        <p role="status" className="mt-5 flex items-center gap-2 text-sm">
          <Loader2 className="size-4 animate-spin" aria-hidden="true" />
          Loading the showing agents…
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
      {notice && (
        <p role={notice.ok ? "status" : "alert"} className={cn("mt-4 text-sm", !notice.ok && "text-destructive")}>
          {notice.message}
        </p>
      )}

      {editing && (
        <div className="mt-5">
          <AgentForm
            editing={editing}
            connections={connections}
            saving={saving}
            problems={problems}
            onChange={setEditing}
            onSave={() => void save()}
            onCancel={() => {
              setEditing(null);
              setProblems([]);
            }}
          />
        </div>
      )}

      {agents && agents.length === 0 && !editing && (
        <div className="mt-5 rounded-lg border border-dashed p-5 text-sm">
          <p className="flex items-center gap-2 font-medium">
            <UsersRound className="size-4" aria-hidden="true" />
            No showing agents yet.
          </p>
          <p className="mt-1 text-muted-foreground">Add the people who host showings. No unit offers times until one is active.</p>
          {!isProduction && (
            <div className="mt-3">
              <Button variant="outline" size="sm" disabled={loadingSamples} onClick={() => void loadSamples()} className="gap-1.5">
                {loadingSamples && <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />}
                Load sample agents
              </Button>
              <p className="mt-2 text-xs text-muted-foreground">
                Two made-up agents with a made-up week of busy times, to try open times here. Never available in
                production.
              </p>
            </div>
          )}
        </div>
      )}

      {agents && agents.length > 0 && (
        <ul className="mt-5 space-y-3">
          {agents.map((agent) => (
            <AgentItem
              key={agent.id}
              agent={agent}
              busy={toggling === agent.id || saving}
              onEdit={() => edit(agent)}
              onToggle={() => void toggle(agent)}
            />
          ))}
        </ul>
      )}
    </section>
  );
}
