import { useEffect } from "react";
import { ChevronRight } from "lucide-react";
import { Link } from "react-router";

import { Skeleton } from "~/components/ui/skeleton";
import { cn } from "~/lib/utils";
import { useTeamCounts } from "~/sites/team/components/team-counts";
import { useTeamAdmin } from "~/sites/team/components/team-nav";
import {
  approvalsText,
  conversationHref,
  conversationWith,
  openText,
  type Approvals,
} from "~/sites/team/lib/team-home";
import { teamTitle } from "~/sites/team/lib/team-meta";
import { visibleSections } from "~/sites/team/lib/team-sections";

export const meta = () => [{ title: teamTitle() }];

function formatWhen(value: string | null): string {
  if (!value) return "";
  return new Date(value).toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" });
}

/** A small label on a card: what is waiting there. */
function Pill({ tone = "plain", children }: { tone?: "plain" | "attention" | "alert" | "quiet"; children: React.ReactNode }) {
  return (
    <span
      className={cn(
        "inline-flex h-6 items-center whitespace-nowrap rounded-full px-2 text-xs font-medium tabular-nums",
        tone === "plain" && "bg-muted text-foreground",
        tone === "attention" && "bg-amber-500/15 text-amber-700 dark:text-amber-300",
        tone === "alert" && "bg-red-500/15 text-red-700 dark:text-red-300",
        tone === "quiet" && "px-0 font-normal text-muted-foreground",
      )}
    >
      {children}
    </span>
  );
}

/** What the Sernia AI and Tasks cards say is waiting, as the navigation's badges do. */
function CardStatus({ url }: { url: string }) {
  const { approvals, tasks } = useTeamCounts();
  if (url === "/team/chat") {
    if (approvals.state === "loading") return <Skeleton className="h-6 w-24 rounded-full" aria-label="Checking approvals" />;
    if (approvals.state === "failed") return <Pill tone="quiet">Approvals not checked</Pill>;
    const count = approvals.value.count;
    return <Pill tone={count ? "attention" : "quiet"}>{approvalsText(count)}</Pill>;
  }
  if (url === "/team/tasks") {
    if (tasks.state === "loading") return <Skeleton className="h-6 w-24 rounded-full" aria-label="Counting tasks" />;
    if (tasks.state === "failed") return <Pill tone="quiet">Tasks not counted</Pill>;
    const counts = tasks.value;
    return (
      <>
        <Pill tone={counts.open || counts.partial.length ? "plain" : "quiet"}>{openText(counts)}</Pill>
        {counts.overdue > 0 && <Pill tone="alert">{counts.overdue} overdue</Pill>}
      </>
    );
  }
  return null;
}

/**
 * The conversations with something to approve: a message to send, a listing
 * or a job to change. Shown only while there is one.
 */
function ApprovalsList({ approvals }: { approvals: Approvals }) {
  return (
    <section aria-labelledby="approvals-title" className="space-y-2">
      <h2 id="approvals-title" className="flex items-center gap-2 text-base font-semibold">
        Approvals
        <Pill tone="attention">{approvals.count}</Pill>
      </h2>
      <ul className="divide-y rounded-lg border">
        {approvals.first.map((conversation) => {
          const who = conversationWith(conversation);
          return (
            <li key={conversation.conversation_id}>
              <Link
                to={conversationHref(conversation.conversation_id, conversation.lead_thread_id)}
                className="flex items-center gap-3 px-4 py-3 hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium">{conversation.preview || "Empty conversation"}</span>
                  <span className="block truncate text-xs text-muted-foreground">
                    {[who, formatWhen(conversation.updated_at ?? conversation.created_at)].filter(Boolean).join(" · ")}
                  </span>
                </span>
                <ChevronRight className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
              </Link>
            </li>
          );
        })}
      </ul>
      {approvals.count > approvals.first.length && (
        <p className="text-xs text-muted-foreground">
          {approvals.count - approvals.first.length} more in{" "}
          <Link to="/team/chat" className="underline underline-offset-4">
            Sernia AI
          </Link>
          .
        </p>
      )}
    </section>
  );
}

export default function TeamHome() {
  const admin = useTeamAdmin();
  const { approvals, refresh } = useTeamCounts();
  // The team home reads what is waiting afresh each time it opens.
  useEffect(() => refresh(0), [refresh]);
  return (
    // The team layout's SidebarInset is the page's <main>.
    <div className="mx-auto w-full max-w-3xl space-y-8 px-4 py-8 sm:px-6 sm:py-10">
      <h1 className="text-2xl font-semibold tracking-tight">Sernia Capital team</h1>
      {approvals.state === "loaded" && approvals.value.count > 0 && <ApprovalsList approvals={approvals.value} />}
      <section aria-labelledby="pages-title" className="space-y-3">
        <h2 id="pages-title" className="text-base font-semibold">
          Team pages
        </h2>
        <ul className="grid gap-3 sm:grid-cols-2">
          {visibleSections(admin).map((section) => (
            <li key={section.url}>
              <Link
                to={section.url}
                className="flex h-full gap-3 rounded-lg border p-4 hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <section.icon className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
                <span className="min-w-0 flex-1">
                  <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                    <span className="mr-auto text-sm font-medium">{section.title}</span>
                    <CardStatus url={section.url} />
                  </span>
                  <span className="mt-1 block text-sm text-muted-foreground">{section.description}</span>
                </span>
              </Link>
            </li>
          ))}
        </ul>
        <p className="text-sm">
          <Link to="/team/leads/overview" className="underline underline-offset-4">
            How it works
          </Link>
          <span className="text-muted-foreground">: how leads become replies and showings, and who is told what.</span>
        </p>
      </section>
    </div>
  );
}
