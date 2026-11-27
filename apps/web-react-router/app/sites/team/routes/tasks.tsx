import { useCallback, useEffect, useRef, useState } from "react";
import { useAuth } from "@clerk/react-router";
import { ExternalLink, Loader2, RefreshCw } from "lucide-react";

import { CreateTask } from "~/sites/team/components/create-task";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "~/components/ui/tabs";
import { Button } from "~/components/ui/button";
import { Skeleton } from "~/components/ui/skeleton";
import { cn } from "~/lib/utils";
import { useTeamCounts } from "~/sites/team/components/team-counts";
import { teamTitle } from "~/sites/team/lib/team-meta";
import {
  assigneeText,
  openCount,
  type TaskDraft,
  dueLabel,
  SOURCE_NAME,
  statusText,
  taskKey,
  withStatus,
  type TasksView,
  type TeamTask,
} from "~/sites/team/lib/team-tasks";

export const meta = () => [{ title: teamTitle("Tasks") }];

type Loaded = { state: "loading" } | { state: "failed" } | { state: "loaded"; view: TasksView };

/** What the person did to a task on this page: where it stood before, for Undo. */
type Done = { before: string };

const API = "/api/team-tasks";

function useTasks() {
  const { getToken } = useAuth();
  const [loaded, setLoaded] = useState<Loaded>({ state: "loading" });
  // Tasks completed here stay in the list, struck through, until the next
  // load: the person can see what they did and take it back.
  const [done, setDone] = useState<Record<string, Done>>({});
  const [working, setWorking] = useState<Record<string, boolean>>({});
  const [problems, setProblems] = useState<Record<string, string>>({});
  const [refreshing, setRefreshing] = useState(false);
  // A reload and a status change never overlap. A reload that began before a
  // change finished would bring back the task as it was, show it open again
  // and drop its Undo. The buttons are disabled to match (see `changing`).
  const changes = useRef(0);
  const reloading = useRef(false);

  const load = useCallback(
    async (quiet: boolean) => {
      if (changes.current > 0 || reloading.current) return;
      reloading.current = true;
      if (quiet) setRefreshing(true);
      else setLoaded({ state: "loading" });
      try {
        const token = await getToken();
        const response = await fetch(API, { headers: { Authorization: `Bearer ${token}` } });
        if (!response.ok) throw new Error(`Tasks answered ${response.status}`);
        setLoaded({ state: "loaded", view: (await response.json()) as TasksView });
        setDone({});
        setProblems({});
      } catch {
        setLoaded({ state: "failed" });
      } finally {
        reloading.current = false;
        setRefreshing(false);
      }
    },
    [getToken],
  );
  useEffect(() => {
    void load(false);
  }, [load]);

  const setCompleted = useCallback(
    async (task: TeamTask, completed: boolean) => {
      if (reloading.current) return;
      const key = taskKey(task);
      changes.current += 1;
      setWorking((current) => ({ ...current, [key]: true }));
      setProblems(({ [key]: _, ...rest }) => rest);
      try {
        const token = await getToken();
        const response = await fetch(`${API}/${task.source}/${encodeURIComponent(task.id)}/status`, {
          method: "POST",
          headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
          body: JSON.stringify(completed ? { completed } : { completed, restore: done[key]?.before ?? null }),
        });
        if (!response.ok) {
          const body = (await response.json().catch(() => null)) as { detail?: unknown } | null;
          throw new Error(typeof body?.detail === "string" ? body.detail : "That did not work. Try again.");
        }
        const changed = (await response.json()) as TeamTask;
        setLoaded((current) =>
          current.state === "loaded"
            ? {
                state: "loaded",
                view: {
                  ...current.view,
                  tasks: current.view.tasks.map((item) => (taskKey(item) === key ? withStatus(item, changed) : item)),
                },
              }
            : current,
        );
        setDone(({ [key]: _, ...rest }) => (completed ? { ...rest, [key]: { before: task.status } } : rest));
      } catch (error) {
        setProblems((current) => ({
          ...current,
          [key]: error instanceof Error && error.message ? error.message : "That did not work. Try again.",
        }));
      } finally {
        changes.current -= 1;
        setWorking(({ [key]: _, ...rest }) => rest);
      }
    },
    [done, getToken],
  );

  async function create(draft: TaskDraft) {
    if (reloading.current) throw new Error("Wait for the refresh to finish.");
    changes.current += 1;
    try {
      const token = await getToken();
      const response = await fetch(`${API}/clickup`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify(draft),
      }).catch(() => {
        throw new Error("Could not confirm creation. Check ClickUp before trying again.");
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        throw new Error(typeof body?.detail === "string" ? body.detail : "Could not confirm creation. Check ClickUp before trying again.");
      }
      const task: TeamTask = await response.json();
      setLoaded((current) => {
        if (current.state !== "loaded") return current;
        const tasks = [...current.view.tasks, task].sort((a, b) => {
          if (!a.due || !b.due) return Number(Boolean(b.due)) - Number(Boolean(a.due));
          return Date.parse(a.due) - Date.parse(b.due);
        });
        return { ...current, view: { ...current.view, tasks } };
      });
      return task;
    } finally {
      changes.current -= 1;
    }
  }

  const changing = Object.keys(working).length > 0;
  return { loaded, refreshing, changing, working, problems, reload: load, setCompleted, create };
}

const DUE_TONE = {
  overdue: "text-red-600 dark:text-red-400 font-medium",
  today: "text-amber-600 dark:text-amber-400 font-medium",
  upcoming: "text-foreground",
  none: "text-muted-foreground",
} as const;

function TaskRow(props: {
  task: TeamTask;
  listName: string;
  working: boolean;
  /** The list is being reloaded: no change can start until it is back. */
  paused: boolean;
  problem: string | undefined;
  onComplete: () => void;
  onUndo: () => void;
}) {
  const { task } = props;
  const [open, setOpen] = useState(false);
  const due = dueLabel(task.due, new Date());
  const status = statusText(task);
  const long = task.description.length > 160 || task.description.includes("\n");
  return (
    <li className={cn("flex flex-col gap-3 px-4 py-4 sm:flex-row sm:items-start sm:gap-4", task.completed && "bg-muted/40")}>
      <div className="min-w-0 flex-1 space-y-1.5">
        <p className={cn("font-medium leading-snug", task.completed && "text-muted-foreground line-through")}>{task.title}</p>
        {task.description && (
          <p
            className={cn(
              "whitespace-pre-line break-words text-sm text-muted-foreground",
              !open && "line-clamp-2",
            )}
          >
            {task.description}
          </p>
        )}
        {task.description && long && (
          <button
            type="button"
            className="text-xs font-medium underline underline-offset-4"
            aria-expanded={open}
            onClick={() => setOpen((value) => !value)}
          >
            {open ? "Show less" : "Show more"}
          </button>
        )}
        <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
          <span className={DUE_TONE[due.tone]}>{due.text}</span>
          <span className="text-muted-foreground">{assigneeText(task.assignees)}</span>
          {status && <span className="text-muted-foreground">{status}</span>}
          <span className="rounded border px-1.5 py-0.5 text-xs font-medium text-muted-foreground">{props.listName}</span>
          {task.url && (
            <a
              href={task.url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 underline underline-offset-4"
            >
              Open in {SOURCE_NAME[task.source]}
              <ExternalLink className="size-3.5" aria-hidden="true" />
            </a>
          )}
        </p>
        {props.problem && (
          <p role="alert" className="text-sm font-medium text-red-600 dark:text-red-400">
            {props.problem}
          </p>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-2 sm:pt-0.5">
        {task.completed ? (
          <>
            <span className="text-sm text-muted-foreground">Completed</span>
            <Button variant="ghost" size="sm" className="gap-1.5" disabled={props.working || props.paused} onClick={props.onUndo}>
              {props.working && <Loader2 className="size-4 animate-spin" aria-hidden="true" />}
              {props.working ? "Reopening…" : "Undo"}
            </Button>
          </>
        ) : (
          <Button variant="outline" size="sm" className="gap-1.5" disabled={props.working || props.paused} onClick={props.onComplete}>
            {props.working && <Loader2 className="size-4 animate-spin" aria-hidden="true" />}
            {props.working ? "Completing…" : "Mark complete"}
          </Button>
        )}
      </div>
    </li>
  );
}

export default function TeamTasks() {
  const { loaded, refreshing, changing, working, problems, reload, setCompleted, create } = useTasks();
  const [scope, setScope] = useState("mine");
  const [list, setList] = useState("all");
  const [notice, setNotice] = useState("");
  const { reportTasks } = useTeamCounts();
  useEffect(() => {
    if (loaded.state === "loaded") reportTasks(loaded.view);
  }, [loaded, reportTasks]);

  const view = loaded.state === "loaded" ? loaded.view : null;
  const lists = view?.clickup_lists ?? [];
  const scoped = (view?.tasks ?? []).filter((task) => list === "all" || (task.list_id ?? task.source) === list);
  const mine = scoped.filter((task) => task.mine);
  const shown = scope === "mine" ? mine : scoped;
  const listName = (id: string | null) => lists.find((item) => item.id === id)?.name;
  const warnings = lists.filter((item) => item.state !== "ready").map((item) =>
    item.state === "unavailable" ? `${item.name} could not be loaded.` : `Some ${item.name} tasks may be missing.`);
  if (view?.unavailable.includes("quo")) warnings.push("Quo could not be loaded.");
  if (view?.incomplete.includes("quo")) warnings.push("Some Quo tasks may be missing.");
  const partial = list === "all" ? warnings.length > 0 : list === "quo"
    ? Boolean(view?.unavailable.includes("quo") || view?.incomplete.includes("quo"))
    : lists.find((item) => item.id === list)?.state !== "ready";

  async function addTask(draft: TaskDraft) {
    const task = await create(draft);
    if (!task.mine) setScope("all");
    if (list !== "all") setList(draft.list_id);
    setNotice(`Created “${task.title}” in ${listName(draft.list_id)}.`);
  }
  return (
    // The team layout's SidebarInset is the page's <main>.
    <div className="mx-auto w-full max-w-3xl space-y-6 px-4 py-8 sm:px-6 sm:py-10">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <h1 className="text-2xl font-semibold tracking-tight">Tasks</h1>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            className="gap-1.5"
            disabled={refreshing || changing || loaded.state === "loading"}
            onClick={() => void reload(true)}
          >
            <RefreshCw className={cn("size-4", refreshing && "animate-spin")} aria-hidden="true" />
            {refreshing ? "Refreshing…" : "Refresh"}
          </Button>
          {lists.length > 0 && <CreateTask lists={lists} defaultList={listName(list) ? list : lists[0].id} disabled={refreshing} onCreate={addTask} />}
        </div>
      </div>

      {loaded.state === "loading" && (
        <div className="space-y-2" aria-busy="true" aria-label="Loading tasks">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
        </div>
      )}

      {loaded.state === "failed" && (
        <div role="alert" className="flex flex-wrap items-center gap-3 rounded-lg border p-4 text-sm">
          <span className="text-muted-foreground">The tasks could not be loaded.</span>
          <Button variant="outline" size="sm" onClick={() => void reload(false)}>
            Try again
          </Button>
        </div>
      )}

      {loaded.state === "loaded" && (
        <>
          {warnings.length > 0 && (
            <p role="alert" className="rounded-lg border border-amber-500/50 p-3 text-sm">{warnings.join(" ")} Refresh to try again.</p>
          )}
          {notice && <p role="status" className="text-sm">{notice}</p>}
          <Tabs value={scope} onValueChange={setScope}>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <TabsList aria-label="Task ownership">
                {[
                  { value: "mine", label: "My Tasks", count: openCount(mine) },
                  { value: "all", label: "All tasks", count: openCount(scoped) },
                ].map(({ value, label, count }) => (
                  <TabsTrigger key={value} value={value} className="gap-2">
                    {label}<span className="text-xs tabular-nums text-muted-foreground">{count}{partial ? "+" : ""}</span>
                  </TabsTrigger>
                ))}
              </TabsList>
              <select aria-label="Filter by list" value={list} onChange={(event) => setList(event.target.value)}
                className="h-9 rounded-md border border-input bg-background px-2 text-sm">
                <option value="all">All lists</option>
                {lists.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                <option value="quo">Quo</option>
              </select>
            </div>
            <TabsContent key={scope} value={scope} className="mt-4">
              {shown.length === 0 ? (
                <p className="rounded-lg border p-4 text-sm text-muted-foreground">
                  {partial ? "No matching tasks in the results available." : scope === "mine" ? "No open tasks assigned to you in this view." : "No open tasks in this view."}
                </p>
              ) : (
                <ul className="divide-y rounded-lg border">
                  {shown.map((task) => {
                    const key = taskKey(task);
                    return (
                      <TaskRow
                        key={key}
                        task={task}
                        listName={listName(task.list_id) ?? SOURCE_NAME[task.source]}
                        working={working[key] === true}
                        paused={refreshing}
                        problem={problems[key]}
                        onComplete={() => void setCompleted(task, true)}
                        onUndo={() => void setCompleted(task, false)}
                      />
                    );
                  })}
                </ul>
              )}
            </TabsContent>
          </Tabs>
        </>
      )}
    </div>
  );
}
