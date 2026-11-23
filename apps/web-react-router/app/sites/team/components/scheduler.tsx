import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useAuth } from "@clerk/react-router";
import { ChevronDown, Loader2, Play, RefreshCw, Trash2 } from "lucide-react";

import { Button } from "~/components/ui/button";
import { Skeleton } from "~/components/ui/skeleton";
import { cn } from "~/lib/utils";
import {
  clockText,
  durationText,
  failureText,
  isStale,
  nextRun,
  REFRESH_EVERY_MS,
  refreshDue,
  relativeText,
  scheduleWords,
  type NextRun,
  type SchedulerJob,
} from "~/sites/team/lib/scheduler-jobs";

type Loaded =
  | { state: "loading" }
  | { state: "failed"; problem: string }
  | { state: "loaded"; jobs: SchedulerJob[]; loadedAt: number };

/** What happened to the last action on one job, shown on its card. */
type Outcome = { tone: "done" | "failed"; text: string };

type Busy = "run" | "delete";

function jobKey(job: SchedulerJob): string {
  return `${job.service}:${job.id}`;
}

function problemOf(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

/** A server's message as a sentence: "Service unavailable" reads "Service unavailable." */
function sentence(text: string): string {
  return /[.!?]$/.test(text.trim()) ? text.trim() : `${text.trim()}.`;
}

/** The time, ticking every second while the page is visible. */
function useNow(): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const tick = () => {
      if (document.visibilityState === "visible") setNow(Date.now());
    };
    const timer = window.setInterval(tick, 1000);
    document.addEventListener("visibilitychange", tick);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", tick);
    };
  }, []);
  return now;
}

function NextRunLine({ run, loadedAt, now }: { run: NextRun; loadedAt: number; now: number }) {
  const today = new Date(now);
  switch (run.kind) {
    case "none":
      return <span className="text-muted-foreground">{run.text}</span>;
    case "upcoming":
      return (
        <span className="tabular-nums">
          Next run {relativeText(run.at, now)} · {clockText(new Date(run.at), today)}
        </span>
      );
    case "passed":
      return (
        <span className="tabular-nums text-amber-700 dark:text-amber-400">
          Was due {relativeText(run.at, now)} · {clockText(new Date(run.at), today)} · the list has not caught up yet
        </span>
      );
    case "overdue":
      return (
        <span className="font-medium tabular-nums text-red-600 dark:text-red-400">
          Late: due {durationText(loadedAt - run.at)} before the list loaded ({clockText(new Date(run.at), today)}). The
          scheduler may not be running.
        </span>
      );
  }
}

function Detail({ label, children, code = false }: { label: string; children: ReactNode; code?: boolean }) {
  return (
    <>
      <dt className="font-medium text-muted-foreground">{label}</dt>
      <dd className={cn("min-w-0 break-words", code && "whitespace-pre-wrap break-all font-mono text-xs leading-5")}>
        {children}
      </dd>
    </>
  );
}

function shown(value: unknown): string {
  return value === null || value === undefined ? "Not set yet" : String(value);
}

function JobCard(props: {
  job: SchedulerJob;
  loadedAt: number;
  now: number;
  open: boolean;
  busy: Busy | undefined;
  outcome: Outcome | undefined;
  onToggle: () => void;
  onRun: () => void;
  onDelete: () => void;
}) {
  const { job, loadedAt, now, open, busy } = props;
  const panelId = `job-${job.service}-${job.id}`.replace(/[^A-Za-z0-9_-]/g, "-");
  const schedule = scheduleWords(job, new Date(now));
  const run = nextRun(job, loadedAt, now);
  return (
    <li className="rounded-lg border">
      <h3 className="text-base font-normal">
        <button
          type="button"
          aria-expanded={open}
          aria-controls={panelId}
          onClick={props.onToggle}
          className="flex w-full items-start gap-3 rounded-lg p-4 text-left hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
        >
          <span className="min-w-0 flex-1 space-y-1">
            <span className="block font-medium leading-snug">{job.name}</span>
            {job.summary && <span className="block text-sm text-muted-foreground">{job.summary}</span>}
            <span className="flex flex-col gap-0.5 text-sm">
              <span className={cn(!schedule && "break-all font-mono text-xs")}>{schedule ?? job.trigger}</span>
              <NextRunLine run={run} loadedAt={loadedAt} now={now} />
            </span>
          </span>
          <ChevronDown
            className={cn("mt-1 size-4 shrink-0 text-muted-foreground transition-transform", open && "rotate-180")}
            aria-hidden="true"
          />
        </button>
      </h3>
      <div id={panelId} hidden={!open} className="space-y-4 border-t px-4 py-4 text-sm">
        {job.description ? (
          <div className="space-y-2 leading-relaxed">
            {job.description.split(/\n{2,}/).map((paragraph, index) => (
              <p key={index}>{paragraph}</p>
            ))}
          </div>
        ) : (
          <p className="text-muted-foreground">No description: this job was added without one.</p>
        )}

        <section aria-label="Technical details" className="space-y-2">
          <h4 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Technical details</h4>
          <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-4 gap-y-1">
            <Detail label="ID" code>
              {job.id}
            </Detail>
            <Detail label="Trigger" code>
              {job.trigger}
            </Detail>
            <Detail label="Time zone">{job.timezone ?? "Not set"}</Detail>
            <Detail label="Next run" code>
              {job.next_run_time ?? "None"}
            </Detail>
            <Detail label="Function" code>
              {job.func_ref}
            </Detail>
            <Detail label="Service">{job.service}</Detail>
            <Detail label="Args" code>
              {JSON.stringify(job.args)}
            </Detail>
            <Detail label="Kwargs" code>
              {JSON.stringify(job.kwargs)}
            </Detail>
            <Detail label="Coalesce">{shown(job.coalesce)}</Detail>
            <Detail label="Executor">{shown(job.executor)}</Detail>
            <Detail label="Max instances">{shown(job.max_instances)}</Detail>
            <Detail label="Misfire grace">
              {job.misfire_grace_time === null ? "Not set yet" : `${job.misfire_grace_time} s`}
            </Detail>
            <Detail label="Pending">{String(job.pending)}</Detail>
          </dl>
        </section>

        <div className="flex flex-wrap gap-2">
          <Button size="sm" className="gap-1.5" disabled={busy !== undefined} onClick={props.onRun}>
            {busy === "run" ? (
              <Loader2 className="size-4 animate-spin" aria-hidden="true" />
            ) : (
              <Play className="size-4" aria-hidden="true" />
            )}
            {busy === "run" ? "Starting…" : "Run now"}
          </Button>
          {job.service === "apscheduler" && (
            <Button
              size="sm"
              variant="destructive"
              className="gap-1.5"
              disabled={busy !== undefined}
              onClick={props.onDelete}
            >
              {busy === "delete" ? (
                <Loader2 className="size-4 animate-spin" aria-hidden="true" />
              ) : (
                <Trash2 className="size-4" aria-hidden="true" />
              )}
              {busy === "delete" ? "Deleting…" : "Delete"}
            </Button>
          )}
        </div>
        {props.outcome && (
          <p
            role={props.outcome.tone === "failed" ? "alert" : "status"}
            className={cn(
              props.outcome.tone === "failed" ? "font-medium text-red-600 dark:text-red-400" : "text-muted-foreground",
            )}
          >
            {props.outcome.text}
          </p>
        )}
      </div>
    </li>
  );
}

interface SchedulerProps {
  apiBaseUrl: string;
}

export function Scheduler({ apiBaseUrl }: SchedulerProps) {
  const { getToken, isLoaded, isSignedIn } = useAuth();
  const [loaded, setLoaded] = useState<Loaded>({ state: "loading" });
  const [refreshing, setRefreshing] = useState(false);
  const [refreshProblem, setRefreshProblem] = useState<string | null>(null);
  const [openJobs, setOpenJobs] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState<Record<string, Busy>>({});
  const [outcomes, setOutcomes] = useState<Record<string, Outcome>>({});
  const [notice, setNotice] = useState<string | null>(null);
  const now = useNow();
  // The latest load wins; loads in flight are counted so the timer does not stack them.
  const sequence = useRef(0);
  const inFlight = useRef(0);
  const lastAttemptAt = useRef(0);

  const fetchWithAuth = useCallback(
    async (url: string, init: RequestInit, retryOn401 = true) => {
      const token = await getToken();
      if (!token) {
        throw new Error("Not authenticated (no token available).");
      }

      const headers: HeadersInit = {
        "Content-Type": "application/json",
        ...(init.headers ?? {}),
        Authorization: `Bearer ${token}`,
      };

      const response = await fetch(url, { ...init, headers });
      if (response.status === 401 && retryOn401) {
        // Clerk tokens can rotate; retry once with a fresh token call.
        const token2 = await getToken();
        if (!token2) return response;
        const headers2: HeadersInit = {
          "Content-Type": "application/json",
          ...(init.headers ?? {}),
          Authorization: `Bearer ${token2}`,
        };
        return await fetch(url, { ...init, headers: headers2 });
      }
      return response;
    },
    [getToken],
  );

  /** Load the list. "quiet" keeps the list on screen while it reloads. */
  const load = useCallback(
    async (how: "first" | "quiet") => {
      if (!isSignedIn) {
        setLoaded({ state: "failed", problem: "Not signed in." });
        return;
      }
      const mine = ++sequence.current;
      inFlight.current += 1;
      lastAttemptAt.current = Date.now();
      if (how === "first") setLoaded({ state: "loading" });
      setRefreshing(true);
      try {
        const response = await fetchWithAuth(`${apiBaseUrl}/schedulers/get_jobs`, { method: "GET" });
        if (!response.ok) {
          throw new Error(failureText(await response.json().catch(() => null), response.status));
        }
        const jobs = (await response.json()) as SchedulerJob[];
        if (mine !== sequence.current) return;
        setLoaded({ state: "loaded", jobs, loadedAt: Date.now() });
        setRefreshProblem(null);
      } catch (error) {
        if (mine !== sequence.current) return;
        const problem = problemOf(error, "The list could not be loaded.");
        setLoaded((current) => (current.state === "loaded" ? current : { state: "failed", problem }));
        setRefreshProblem(problem);
      } finally {
        inFlight.current -= 1;
        if (mine === sequence.current) setRefreshing(false);
      }
    },
    [apiBaseUrl, fetchWithAuth, isSignedIn],
  );

  const started = useRef(false);
  useEffect(() => {
    if (!isLoaded || started.current) return;
    started.current = true;
    void load("first");
  }, [isLoaded, load]);

  // Reload on its own while the page is visible; `now` stops ticking while it is hidden.
  useEffect(() => {
    if (loaded.state !== "loaded" || inFlight.current > 0) return;
    if (document.visibilityState !== "visible") return;
    if (refreshDue(lastAttemptAt.current, now)) void load("quiet");
  }, [now, loaded.state, load]);

  const toggleJob = (key: string) => {
    setOpenJobs((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const startAction = (key: string, action: Busy) => {
    setBusy((current) => ({ ...current, [key]: action }));
    setOutcomes(({ [key]: _, ...rest }) => rest);
    setNotice(null);
  };
  const endAction = (key: string, outcome: Outcome | null) => {
    setBusy(({ [key]: _, ...rest }) => rest);
    if (outcome) setOutcomes((current) => ({ ...current, [key]: outcome }));
  };

  const handleRunJobNow = async (job: SchedulerJob) => {
    const key = jobKey(job);
    startAction(key, "run");
    try {
      const response = await fetchWithAuth(
        `${apiBaseUrl}/schedulers/run_job_now/${job.service}/${encodeURIComponent(job.id)}`,
        { method: "GET" },
      );
      if (!response.ok) {
        throw new Error(failureText(await response.json().catch(() => null), response.status));
      }
      endAction(key, {
        tone: "done",
        text: "Asked the scheduler to run it now. The result shows in the logs, not on this page.",
      });
      void load("quiet");
    } catch (error) {
      endAction(key, { tone: "failed", text: `It did not start: ${sentence(problemOf(error, "unknown error"))}` });
    }
  };

  const handleDeleteJob = async (job: SchedulerJob) => {
    if (job.service !== "apscheduler") return;

    const ok = window.confirm(
      `Delete APScheduler job "${job.name || job.id}"?\n\nThis removes it from the persisted job store.`,
    );
    if (!ok) return;

    const key = jobKey(job);
    startAction(key, "delete");
    try {
      const response = await fetchWithAuth(
        `${apiBaseUrl}/schedulers/delete_job/${job.service}/${encodeURIComponent(job.id)}`,
        { method: "DELETE" },
      );
      if (!response.ok) {
        throw new Error(failureText(await response.json().catch(() => null), response.status));
      }
      endAction(key, null);
      setNotice(`Deleted "${job.name || job.id}".`);
      // A load already in flight may still list it; this one supersedes it.
      setLoaded((current) =>
        current.state === "loaded"
          ? { ...current, jobs: current.jobs.filter((item) => jobKey(item) !== key) }
          : current,
      );
      void load("quiet");
    } catch (error) {
      endAction(key, { tone: "failed", text: `It was not deleted: ${sentence(problemOf(error, "unknown error"))}` });
    }
  };

  const header = (
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">Scheduler</h1>
        <p className="text-sm text-muted-foreground">
          The jobs the API runs on its own schedule. Open one to see what it does. Times are Eastern.
        </p>
      </div>
      <Button
        variant="outline"
        size="sm"
        className="gap-1.5"
        disabled={refreshing || loaded.state === "loading"}
        onClick={() => void load(loaded.state === "loaded" ? "quiet" : "first")}
      >
        <RefreshCw className={cn("size-4", refreshing && "animate-spin")} aria-hidden="true" />
        {refreshing ? "Refreshing…" : "Refresh"}
      </Button>
    </div>
  );

  if (loaded.state === "loading") {
    return (
      <div className="space-y-6">
        {header}
        <div className="space-y-2" aria-busy="true" aria-label="Loading jobs">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-24 w-full" />
        </div>
      </div>
    );
  }

  if (loaded.state === "failed") {
    return (
      <div className="space-y-6">
        {header}
        <div role="alert" className="flex flex-wrap items-center gap-3 rounded-lg border p-4 text-sm">
          <span className="text-muted-foreground">The jobs could not be loaded: {sentence(loaded.problem)}</span>
          <Button variant="outline" size="sm" disabled={refreshing} onClick={() => void load("first")}>
            Try again
          </Button>
        </div>
      </div>
    );
  }

  const { jobs, loadedAt } = loaded;
  const stale = isStale(loadedAt, now);
  return (
    <div className="space-y-6">
      {header}

      <p className="text-xs text-muted-foreground">
        Updated {relativeText(loadedAt, now)} ({clockText(new Date(loadedAt), new Date(now))}). Refreshes every{" "}
        {REFRESH_EVERY_MS / 1000} s while this page is open.
        {refreshProblem && !stale && (
          <span role="alert" className="block font-medium text-red-600 dark:text-red-400">
            The last refresh failed: {sentence(refreshProblem)} It tries again every {REFRESH_EVERY_MS / 1000} s.
          </span>
        )}
      </p>

      {stale && (
        <div
          role="status"
          className="flex flex-wrap items-center gap-3 rounded-lg border border-amber-500/50 p-4 text-sm"
        >
          <span>
            This list is {durationText(now - loadedAt)} old
            {refreshing ? ", refreshing now." : refreshProblem ? `. The last refresh failed: ${sentence(refreshProblem)}` : "."}{" "}
            Times on it may have passed.
          </span>
          <Button variant="outline" size="sm" disabled={refreshing} onClick={() => void load("quiet")}>
            Refresh
          </Button>
        </div>
      )}

      {notice && (
        <p role="status" className="rounded-lg border p-4 text-sm">
          {notice}
        </p>
      )}

      {jobs.length === 0 ? (
        <p className="rounded-lg border p-4 text-sm text-muted-foreground">
          No jobs are scheduled here. The API registers its jobs when it starts, and only where background automation
          is on, as it is in production.
        </p>
      ) : (
        <ul className="space-y-3">
          {jobs.map((job) => {
            const key = jobKey(job);
            return (
              <JobCard
                key={key}
                job={job}
                loadedAt={loadedAt}
                now={now}
                open={openJobs.has(key)}
                busy={busy[key]}
                outcome={outcomes[key]}
                onToggle={() => toggleJob(key)}
                onRun={() => void handleRunJobNow(job)}
                onDelete={() => void handleDeleteJob(job)}
              />
            );
          })}
        </ul>
      )}
    </div>
  );
}
