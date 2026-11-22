import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router";
import { CalendarPlus, CircleAlert, CircleCheck, Loader2, RefreshCw, RotateCcw, Unplug } from "lucide-react";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import {
  type CalendarConnection,
  type ConnectionsView,
  CONNECTION_STATUS_LABELS,
  connectResult,
  connectionActions,
  describeLastRead,
  formatWhen,
} from "~/lib/showing-bookings";
import { cn } from "~/lib/utils";
import { useShowingBookingsApi } from "./use-showing-bookings-api";

const STATUS_STYLES: Record<CalendarConnection["status"], string> = {
  connected: "border-green-300 text-green-800 dark:border-green-900 dark:text-green-400",
  lost: "border-red-300 text-red-700 dark:border-red-900 dark:text-red-400",
  revoked: "border-muted-foreground/40 text-muted-foreground",
};

type Busy = { id: number | "new"; action: "connect" | "check" | "disconnect" } | null;

function ConnectionItem({
  connection,
  canConnect,
  setupProblem,
  busy,
  error,
  onConnect,
  onCheck,
  onDisconnect,
}: {
  connection: CalendarConnection;
  canConnect: boolean;
  // Why this environment cannot use any connection, already said above the list.
  setupProblem: string | null;
  busy: Busy;
  error: string | null;
  onConnect: (account: string) => void;
  onCheck: (connection: CalendarConnection) => void;
  onDisconnect: (connection: CalendarConnection) => void;
}) {
  const actions = connectionActions(connection, canConnect);
  const working = busy?.id === connection.id ? busy.action : null;
  const needsReconnect = connection.status !== "connected";
  return (
    <li className={cn("rounded-lg border p-4", connection.status === "lost" && "border-red-300 dark:border-red-900")}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="break-all font-medium">{connection.google_email}</p>
          <p className="mt-0.5 text-sm text-muted-foreground">
            Connected {formatWhen(connection.connected_at)} by {connection.connected_by}
          </p>
        </div>
        <Badge variant="outline" className={cn("shrink-0 font-medium", STATUS_STYLES[connection.status])}>
          {CONNECTION_STATUS_LABELS[connection.status]}
        </Badge>
      </div>

      {connection.status === "connected" && (
        <div className="mt-3 space-y-1 text-sm leading-6">
          <p className="flex items-start gap-1.5">
            {connection.last_read_ok === false ? (
              <CircleAlert className="mt-1 size-4 shrink-0 text-red-600 dark:text-red-400" aria-hidden="true" />
            ) : connection.last_read_ok ? (
              <CircleCheck className="mt-1 size-4 shrink-0 text-green-700 dark:text-green-400" aria-hidden="true" />
            ) : null}
            <span>{describeLastRead(connection)}</span>
          </p>
          {connection.last_read_ok === false && connection.last_error && (
            <p className="break-words text-red-700 dark:text-red-400">{connection.last_error}</p>
          )}
          {!connection.has_showings_calendar && (
            <p className="text-muted-foreground">No Sernia showings calendar yet. Reconnect to make one.</p>
          )}
        </div>
      )}
      {connection.status === "lost" && (
        <p className="mt-3 break-words text-sm leading-6 text-red-700 dark:text-red-400">
          {connection.last_error ?? "Google no longer accepts this connection."} Until it is reconnected, its calendars
          count as busy.
        </p>
      )}
      {connection.status === "revoked" && (
        <p className="mt-3 text-sm leading-6 text-muted-foreground">
          Disconnected. The app can no longer read its calendars; the Sernia showings calendar stays in the account.
        </p>
      )}
      {connection.status === "connected" && !connection.usable_here && connection.unusable_reason && (
        <p className="mt-3 break-words rounded-md bg-amber-50 p-3 text-sm leading-6 text-amber-900 dark:bg-amber-950/30 dark:text-amber-200">
          <strong className="font-medium">Not usable here.</strong>{" "}
          {connection.unusable_reason === setupProblem
            ? "Calendar connections are not set up in this environment."
            : connection.unusable_reason}{" "}
          Its calendars count as busy in this environment.
        </p>
      )}

      {connection.reconnect_adds_permissions && (
        <p className="mt-3 text-xs leading-5 text-muted-foreground">
          Reconnect to let the showing agent form list the team&rsquo;s calendars and those shared with this account. All
          else works without it.
        </p>
      )}

      {(actions.check || actions.reconnect || actions.disconnect) && (
        <div className="mt-4 flex flex-wrap gap-2">
          {actions.check && (
            <Button
              variant="outline"
              size="sm"
              className="gap-2"
              disabled={busy !== null}
              aria-label={`Check ${connection.google_email} now`}
              onClick={() => onCheck(connection)}
            >
              {working === "check" ? (
                <Loader2 className="size-4 animate-spin" aria-hidden="true" />
              ) : (
                <RefreshCw className="size-4" aria-hidden="true" />
              )}
              Check now
            </Button>
          )}
          {actions.reconnect && (
            <Button
              variant={needsReconnect ? "default" : "outline"}
              size="sm"
              className="gap-2"
              disabled={busy !== null}
              aria-label={`Reconnect ${connection.google_email}`}
              onClick={() => onConnect(connection.google_email)}
            >
              {working === "connect" ? (
                <Loader2 className="size-4 animate-spin" aria-hidden="true" />
              ) : (
                <RotateCcw className="size-4" aria-hidden="true" />
              )}
              Reconnect
            </Button>
          )}
          {actions.disconnect && (
            <Button
              variant="outline"
              size="sm"
              className="gap-2 text-red-700 hover:text-red-800 dark:text-red-400"
              disabled={busy !== null}
              aria-label={`Disconnect ${connection.google_email}`}
              onClick={() => onDisconnect(connection)}
            >
              {working === "disconnect" ? (
                <Loader2 className="size-4 animate-spin" aria-hidden="true" />
              ) : (
                <Unplug className="size-4" aria-hidden="true" />
              )}
              Disconnect
            </Button>
          )}
        </div>
      )}
      {error && (
        <p role="alert" className="mt-3 break-words rounded-md border border-destructive/50 p-3 text-sm leading-6">
          {error}
        </p>
      )}
    </li>
  );
}

export function ConnectionsSection() {
  const api = useShowingBookingsApi();
  const sectionRef = useRef<HTMLElement>(null);
  const [searchParams, setSearchParams] = useSearchParams();
  const [view, setView] = useState<ConnectionsView | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<Busy>(null);
  // The section's own trouble (loading, a new connection), and the trouble with one row.
  const [error, setError] = useState<string | null>(null);
  const [rowError, setRowError] = useState<{ id: number; message: string } | null>(null);
  // How the last connect attempt went, from Google's callback.
  const [result, setResult] = useState(() => connectResult(searchParams.get("calendar")));

  // Show the callback's result once, then take it out of the address so a reload does not repeat it.
  useEffect(() => {
    if (!searchParams.has("calendar")) return;
    sectionRef.current?.scrollIntoView({ block: "start" });
    setSearchParams(
      (params) => {
        params.delete("calendar");
        return params;
      },
      { replace: true, preventScrollReset: true },
    );
  }, [searchParams, setSearchParams]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setRowError(null);
    const response = await api<ConnectionsView>("/calendar/connections", {}, "Could not load calendar connections");
    if (response.ok) setView(response.body);
    else setError(response.message);
    setLoading(false);
  }, [api]);

  useEffect(() => {
    void load();
  }, [load]);

  function replace(updated: CalendarConnection) {
    setView((current) =>
      current && {
        ...current,
        connections: current.connections.map((item) => (item.id === updated.id ? updated : item)),
      },
    );
  }

  // A new action replaces whatever the last one left on screen.
  function begin(next: NonNullable<Busy>) {
    setBusy(next);
    setError(null);
    setRowError(null);
    setResult(null);
  }

  function fail(id: number | "new", message: string) {
    if (id === "new") setError(message);
    else setRowError({ id, message });
    setBusy(null);
  }

  async function connect(account?: string) {
    const target = account ? view?.connections.find((item) => item.google_email === account) : undefined;
    const id = target?.id ?? "new";
    begin({ id, action: "connect" });
    const response = await api<{ url: string }>(
      "/calendar/connect",
      { method: "POST", body: JSON.stringify({ origin: window.location.origin, ...(account ? { account } : {}) }) },
      "Could not start the connection",
    );
    if (response.ok) {
      // Google's screen, then back here through the callback.
      window.location.assign(response.body.url);
      return;
    }
    fail(id, response.message);
  }

  async function check(connection: CalendarConnection) {
    begin({ id: connection.id, action: "check" });
    const response = await api<CalendarConnection>(
      `/calendar/connections/${connection.id}/check`,
      { method: "POST" },
      "Could not check the connection",
    );
    if (!response.ok) return fail(connection.id, response.message);
    replace(response.body);
    setBusy(null);
  }

  async function disconnect(connection: CalendarConnection) {
    if (
      !window.confirm(
        `Disconnect ${connection.google_email}? The app stops reading its calendars, and agents read through it count as busy until it is connected again.`,
      )
    ) {
      return;
    }
    begin({ id: connection.id, action: "disconnect" });
    const response = await api<CalendarConnection>(
      `/calendar/connections/${connection.id}/disconnect`,
      { method: "POST" },
      "Could not disconnect",
    );
    if (!response.ok) return fail(connection.id, response.message);
    replace(response.body);
    setBusy(null);
  }

  const canConnect = Boolean(view?.can_connect);

  return (
    <section
      ref={sectionRef}
      id="calendar-connections"
      aria-labelledby="connections-heading"
      className="scroll-mt-4 rounded-xl border p-5 sm:p-6"
    >
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0 sm:flex-1">
          <h3 id="connections-heading" className="text-xl font-semibold">
            Calendar connections
          </h3>
          <p className="mt-1 text-sm leading-6 text-muted-foreground">
            Google accounts that let the app read free and busy times and hold the Sernia showings calendar. Connect the
            team account once; it reads the calendars agents share with it.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" className="gap-2" disabled={!canConnect || busy !== null} onClick={() => void connect()}>
            {busy?.id === "new" ? (
              <Loader2 className="size-4 animate-spin" aria-hidden="true" />
            ) : (
              <CalendarPlus className="size-4" aria-hidden="true" />
            )}
            Connect a Google account
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="gap-2"
            disabled={loading || busy !== null}
            onClick={() => void load()}
          >
            <RefreshCw className={cn("size-4", loading && "animate-spin")} aria-hidden="true" />
            Reload
          </Button>
        </div>
      </div>

      {result && (
        <p
          role={result.ok ? "status" : "alert"}
          className={cn(
            "mt-4 rounded-lg border p-3 text-sm leading-6",
            result.ok
              ? "border-green-300 bg-green-50 text-green-900 dark:border-green-900 dark:bg-green-950/30 dark:text-green-200"
              : "border-destructive/50",
          )}
        >
          {result.message}
        </p>
      )}
      {view && !view.can_connect && (
        <div className="mt-4 rounded-lg bg-muted p-3 text-sm leading-6">
          <p className="font-medium">Calendar connections are not set up here yet.</p>
          {view.setup_problem && <p className="mt-1 break-words text-muted-foreground">{view.setup_problem}</p>}
        </div>
      )}
      {error && (
        <p role="alert" className="mt-4 break-words rounded-lg border border-destructive/50 p-3 text-sm leading-6">
          {error}
        </p>
      )}
      {loading && view === null && (
        <p role="status" className="mt-4 flex items-center gap-2 text-sm">
          <Loader2 className="size-4 animate-spin" aria-hidden="true" />
          Loading calendar connections…
        </p>
      )}
      {view !== null && view.connections.length === 0 && (
        <p className="mt-4 rounded-lg border border-dashed p-4 text-sm leading-6 text-muted-foreground">
          No Google account is connected yet. Until one is, no open times can be offered.
        </p>
      )}
      {view !== null && view.connections.length > 0 && (
        <ul className="mt-4 space-y-3">
          {view.connections.map((connection) => (
            <ConnectionItem
              key={connection.id}
              connection={connection}
              canConnect={canConnect}
              setupProblem={view.setup_problem}
              busy={busy}
              error={rowError?.id === connection.id ? rowError.message : null}
              onConnect={(account) => void connect(account)}
              onCheck={(item) => void check(item)}
              onDisconnect={(item) => void disconnect(item)}
            />
          ))}
        </ul>
      )}
    </section>
  );
}
