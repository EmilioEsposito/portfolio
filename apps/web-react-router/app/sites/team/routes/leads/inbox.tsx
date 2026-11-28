import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@clerk/react-router";
import { Link } from "react-router";
import { Loader2, RefreshCw } from "lucide-react";

import { Button } from "~/components/ui/button";
import { Skeleton } from "~/components/ui/skeleton";
import { cn } from "~/lib/utils";
import { OutdatedBadge, ShowingBadge, StatusBadge } from "~/sites/team/components/lead-badges";
import {
  LEADS_API,
  type LeadThreadList,
  type LeadThreadRow,
  leadThreadPath,
  listTime,
  refusal,
  waitsOnTeam,
} from "~/sites/team/lib/lead-inbox";
import { teamTitle } from "~/sites/team/lib/team-meta";

export const meta = () => [{ title: teamTitle("Leads") }];

// The leads list (/team/leads): one row per Zillow thread in the shared
// mailbox, laid out like Gmail's list so a row is easy to match to the thread
// there. Threads waiting on the team stand out.

const PAGE = 100;

function ThreadItem({ row, now }: { row: LeadThreadRow; now: Date }) {
  const waiting = waitsOnTeam(row.status);
  return (
    <li>
      <Link
        to={leadThreadPath(row.thread_id)}
        prefetch="intent"
        className={cn(
          "block border-l-4 px-4 py-3 hover:bg-muted/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
          waiting ? "border-l-amber-500 bg-amber-50/60 dark:bg-amber-950/20" : "border-l-transparent",
        )}
      >
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5">
          <span className={cn("text-sm", waiting ? "font-semibold" : "font-medium")}>
            {row.first_name}
            <span className="ml-1.5 font-normal tabular-nums text-muted-foreground" title={`${row.email_count} emails`}>
              {row.email_count}
            </span>
          </span>
          {row.unit && <span className="text-xs text-muted-foreground">{row.unit}</span>}
          <span className="order-last flex basis-full flex-wrap gap-1.5 sm:order-none sm:basis-auto">
            <StatusBadge status={row.status} waitingSince={row.waiting_since} now={now} />
            {row.draft_outdated && <OutdatedBadge />}
            {row.showing && <ShowingBadge showing={row.showing} />}
          </span>
          <time
            dateTime={row.latest_at ?? row.activity_at}
            className={cn("ml-auto shrink-0 text-xs tabular-nums", waiting ? "font-semibold" : "text-muted-foreground")}
          >
            {listTime(row.latest_at ?? row.activity_at, now)}
          </time>
        </div>
        {/* Gmail's app: the subject, then the latest email, a line each. Wider: one line. */}
        <p className="mt-1 truncate text-sm sm:hidden">
          <span className={waiting ? "font-medium" : undefined}>{row.subject || "(no subject)"}</span>
        </p>
        {row.snippet && (
          <p className="truncate text-sm text-muted-foreground sm:hidden">
            {row.latest_from_sernia ? "Sernia: " : ""}
            {row.snippet}
          </p>
        )}
        <p className="mt-1 hidden truncate text-sm sm:block">
          <span className={waiting ? "font-medium" : undefined}>{row.subject || "(no subject)"}</span>
          {row.snippet && (
            <span className="text-muted-foreground">
              {" "}
              - {row.latest_from_sernia ? "Sernia: " : ""}
              {row.snippet}
            </span>
          )}
        </p>
      </Link>
    </li>
  );
}

function LoadingRows() {
  return (
    <ul role="status" aria-label="Loading leads" className="divide-y rounded-lg border">
      {[0, 1, 2, 3].map((index) => (
        <li key={index} className="space-y-2 px-4 py-3">
          <div className="flex items-center gap-2">
            <Skeleton className="h-4 w-24" />
            <Skeleton className="h-5 w-36 rounded-full" />
            <Skeleton className="ml-auto h-3 w-12" />
          </div>
          <Skeleton className="h-4 w-full max-w-xl" />
        </li>
      ))}
    </ul>
  );
}

export default function LeadInbox() {
  const { getToken } = useAuth();
  const [list, setList] = useState<LeadThreadList | null>(null);
  const [limit, setLimit] = useState(PAGE);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState(() => new Date());

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const token = await getToken();
      const response = await fetch(`${LEADS_API}?limit=${limit}`, { headers: { Authorization: `Bearer ${token}` } });
      const body = await response.json().catch(() => null);
      if (!response.ok) throw new Error(refusal(response.status, body, "Could not load the leads"));
      setList(body as LeadThreadList);
      setNow(new Date());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load the leads.");
    } finally {
      setLoading(false);
    }
  }, [getToken, limit]);

  useEffect(() => {
    void load();
  }, [load]);

  // Read again when the page comes back into view: a draft may have arrived meanwhile.
  useEffect(() => {
    const again = () => {
      if (document.visibilityState === "visible") void load();
    };
    document.addEventListener("visibilitychange", again);
    return () => document.removeEventListener("visibilitychange", again);
  }, [load]);

  const threads = list?.threads ?? [];
  // Oldest first: the one that has waited longest is answered first.
  const waiting = threads
    .filter((row) => waitsOnTeam(row.status))
    .sort((a, b) => (a.waiting_since ?? "").localeCompare(b.waiting_since ?? ""));
  return (
    <section aria-labelledby="leads-heading" className="space-y-4">
      <div className="space-y-1">
        <div className="flex items-center justify-between gap-3">
          <h2 id="leads-heading" className="text-xl font-semibold sm:text-2xl">
            Leads
          </h2>
          <Button variant="outline" size="sm" className="gap-1.5" disabled={loading} onClick={() => void load()}>
            <RefreshCw className={cn("size-3.5", loading && "animate-spin")} aria-hidden="true" />
            Refresh
          </Button>
        </div>
        <p className="max-w-2xl text-sm leading-6 text-muted-foreground">
          Each Zillow thread in all@serniacapital.com, as Gmail lists it
          <span className="hidden sm:inline">: the lead&rsquo;s first name, how many emails, the subject and the latest email</span>.
          Newest activity first.
        </p>
      </div>

      {waiting.length > 0 && (
        <p className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-sm">
          <span className="font-medium text-amber-900 dark:text-amber-200">Waiting on the team:</span>
          {waiting.map((row) => (
            <Link
              key={row.thread_id}
              to={leadThreadPath(row.thread_id)}
              className="underline underline-offset-4"
            >
              {row.first_name} ({row.status === "draft_waiting" ? "draft" : "question"})
            </Link>
          ))}
        </p>
      )}

      {error && (
        <div role="alert" className="rounded-lg border border-destructive/50 p-3 text-sm">
          <p>{error}</p>
          <Button variant="outline" size="sm" className="mt-2" disabled={loading} onClick={() => void load()}>
            Try again
          </Button>
        </div>
      )}

      {!list && loading && <LoadingRows />}

      {list && threads.length === 0 && (
        <p className="rounded-lg border p-4 text-sm text-muted-foreground">
          No lead threads yet. A thread appears here once the lead agent has answered its first Zillow email.
        </p>
      )}

      {threads.length > 0 && (
        <ul aria-label="Lead threads" className="divide-y overflow-hidden rounded-lg border">
          {threads.map((row) => (
            <ThreadItem key={row.thread_id} row={row} now={now} />
          ))}
        </ul>
      )}

      {list?.has_more && (
        <Button variant="outline" size="sm" disabled={loading} onClick={() => setLimit(limit + PAGE)}>
          {loading && <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />}
          Show older threads
        </Button>
      )}
    </section>
  );
}
