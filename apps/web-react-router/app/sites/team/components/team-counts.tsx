import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useAuth } from "@clerk/react-router";
import { useLocation } from "react-router";

import type { ConversationSummary } from "~/sites/team/lib/sernia-conversations";
import { CONVERSATIONS_CHANGED } from "~/sites/team/lib/sernia-conversations";
import { approvalsFrom, taskCounts, type Approvals, type TaskCounts } from "~/sites/team/lib/team-home";
import type { TasksView } from "~/sites/team/lib/team-tasks";

// The approvals waiting on a person and the open tasks, read once for every
// team page under the layout: the navigation shows them as badges, the team
// home on its cards. They are read again on the next page opened or when the
// app comes back to the screen, once a minute at most, and at once on the
// team home, after a change in the chat, and from the Tasks page's own list.

export type Load<T> = { state: "loading" } | { state: "failed" } | { state: "loaded"; value: T };

type TeamCounts = {
  approvals: Load<Approvals>;
  tasks: Load<TaskCounts>;
  /** Reads both again, unless each was read within `maxAge` milliseconds. */
  refresh: (maxAge?: number) => void;
  /** The Tasks page's own list, so the badge follows it without a second read. */
  reportTasks: (view: TasksView) => void;
};

/** How old the counts may be before opening another page reads them again. */
const STALE_AFTER = 60_000;
const TASKS_PAGE = "/team/tasks";

const TeamCountsContext = createContext<TeamCounts | null>(null);

/** One source's reading: kept until a newer one succeeds, so a failed refresh does not blank a badge. */
function useSource<T>(read: (token: string | null) => Promise<T>) {
  const { getToken } = useAuth();
  const [load, setLoad] = useState<Load<T>>({ state: "loading" });
  const at = useRef(0);
  const busy = useRef(false);
  // Told of a change while a reading was on its way: that reading may
  // predate the change, so another follows it.
  const again = useRef(false);
  // A newer answer given another way (the Tasks page's list) wins over a
  // reading that was already on its way.
  const generation = useRef(0);

  /** `changed`: something changed just now, so a reading already on its way does not count. */
  const refresh = useCallback(
    async (maxAge: number, changed = false): Promise<void> => {
      if (busy.current) {
        if (changed) again.current = true;
        return;
      }
      if (Date.now() - at.current < maxAge) return;
      busy.current = true;
      try {
        do {
          again.current = false;
          const started = generation.current;
          try {
            const value = await read(await getToken());
            if (started === generation.current) {
              at.current = Date.now();
              setLoad({ state: "loaded", value });
            }
          } catch {
            if (started === generation.current) setLoad((current) => (current.state === "loaded" ? current : { state: "failed" }));
          }
        } while (again.current);
      } finally {
        busy.current = false;
      }
    },
    [getToken, read],
  );

  const give = useCallback((value: T) => {
    generation.current += 1;
    at.current = Date.now();
    setLoad({ state: "loaded", value });
  }, []);

  return { load, refresh, give };
}

async function readJson<T>(url: string, token: string | null): Promise<T> {
  const response = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
  if (!response.ok) throw new Error(`${url} answered ${response.status}`);
  return (await response.json()) as T;
}

async function readApprovals(token: string | null): Promise<Approvals> {
  const body = await readJson<{ conversations?: ConversationSummary[] }>("/api/sernia-ai/workflow/pending", token);
  return approvalsFrom(body.conversations ?? []);
}

async function readTasks(token: string | null): Promise<TaskCounts> {
  return taskCounts(await readJson<TasksView>("/api/team-tasks", token), new Date());
}

export function TeamCountsProvider({ children }: { children: React.ReactNode }) {
  const approvals = useSource(readApprovals);
  const tasks = useSource(readTasks);
  const { pathname } = useLocation();
  const onTasksPage = pathname.replace(/\/+$/, "") === TASKS_PAGE;
  const onTasksPageRef = useRef(onTasksPage);
  onTasksPageRef.current = onTasksPage;

  const refreshApprovals = approvals.refresh;
  const refreshTasks = tasks.refresh;
  const giveTasks = tasks.give;

  const refresh = useCallback(
    (maxAge = STALE_AFTER) => {
      void refreshApprovals(maxAge);
      // The Tasks page reads its own list and reports it.
      if (!onTasksPageRef.current) void refreshTasks(maxAge);
    },
    [refreshApprovals, refreshTasks],
  );

  // Each page opened, and the app coming back to the screen.
  useEffect(() => {
    refresh();
  }, [pathname, refresh]);
  useEffect(() => {
    const visible = () => {
      if (document.visibilityState === "visible") refresh();
    };
    document.addEventListener("visibilitychange", visible);
    return () => document.removeEventListener("visibilitychange", visible);
  }, [refresh]);

  // The chat says when a conversation changed (a message sent, an approval
  // decided), and the notifications worker when a reply is ready.
  useEffect(() => {
    const changed = () => void refreshApprovals(0, true);
    const pushed = (event: MessageEvent) => {
      if (["response-ready", "notification-click"].includes(event.data?.type)) changed();
    };
    window.addEventListener(CONVERSATIONS_CHANGED, changed);
    navigator.serviceWorker?.addEventListener("message", pushed);
    return () => {
      window.removeEventListener(CONVERSATIONS_CHANGED, changed);
      navigator.serviceWorker?.removeEventListener("message", pushed);
    };
  }, [refreshApprovals]);

  const reportTasks = useCallback((view: TasksView) => giveTasks(taskCounts(view, new Date())), [giveTasks]);

  const value = useMemo(
    () => ({ approvals: approvals.load, tasks: tasks.load, refresh, reportTasks }),
    [approvals.load, tasks.load, refresh, reportTasks],
  );
  return <TeamCountsContext.Provider value={value}>{children}</TeamCountsContext.Provider>;
}

const NO_COUNTS: TeamCounts = {
  approvals: { state: "loading" },
  tasks: { state: "loading" },
  refresh: () => {},
  reportTasks: () => {},
};

/** The counts, or nothing loaded outside the team layout. */
export function useTeamCounts(): TeamCounts {
  return useContext(TeamCountsContext) ?? NO_COUNTS;
}
