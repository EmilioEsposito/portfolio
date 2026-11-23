import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@clerk/react-router";
import { ChevronRight } from "lucide-react";
import { Link } from "react-router";

import { Button } from "~/components/ui/button";
import { Skeleton } from "~/components/ui/skeleton";
import { useTeamAdmin } from "~/sites/team/components/team-nav";
import type { ConversationSummary } from "~/sites/team/lib/sernia-conversations";
import {
  conversationHref,
  conversationWith,
  RECENT_CONVERSATIONS,
  waitingForPerson,
  type Waiting,
} from "~/sites/team/lib/team-home";
import { teamTitle } from "~/sites/team/lib/team-meta";
import { visibleSections } from "~/sites/team/lib/team-sections";

export const meta = () => [{ title: teamTitle() }];

type Loaded = { state: "loading" } | { state: "failed" } | { state: "loaded"; waiting: Waiting };

/** The conversations with something waiting for a person, read as the chat's sidebar reads them. */
function useWaiting() {
  const { getToken } = useAuth();
  const [loaded, setLoaded] = useState<Loaded>({ state: "loading" });
  const load = useCallback(async () => {
    setLoaded({ state: "loading" });
    try {
      const token = await getToken();
      const response = await fetch(`/api/sernia-ai/conversations/history?limit=${RECENT_CONVERSATIONS}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!response.ok) throw new Error(`History answered ${response.status}`);
      const body = (await response.json()) as { conversations?: ConversationSummary[] };
      setLoaded({ state: "loaded", waiting: waitingForPerson(body.conversations ?? []) });
    } catch {
      setLoaded({ state: "failed" });
    }
  }, [getToken]);
  useEffect(() => {
    void load();
  }, [load]);
  return { loaded, reload: load };
}

function formatWhen(value: string | null): string {
  if (!value) return "";
  return new Date(value).toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" });
}

function WaitingSection() {
  const { loaded, reload } = useWaiting();
  return (
    <section aria-labelledby="waiting-title" className="space-y-3">
      <h2 id="waiting-title" className="text-lg font-semibold">
        Waiting for a person
        {loaded.state === "loaded" && (
          <span className="ml-2 rounded-full bg-muted px-2 py-0.5 align-middle text-sm font-medium tabular-nums">
            {loaded.waiting.count}
          </span>
        )}
      </h2>
      {loaded.state === "loading" && (
        <div className="space-y-2" aria-busy="true" aria-label="Loading conversations">
          <Skeleton className="h-14 w-full" />
          <Skeleton className="h-14 w-full" />
        </div>
      )}
      {loaded.state === "failed" && (
        <div role="alert" className="flex flex-wrap items-center gap-3 rounded-lg border p-4 text-sm">
          <span className="text-muted-foreground">The conversations could not be loaded.</span>
          <Button variant="outline" size="sm" onClick={() => void reload()}>
            Try again
          </Button>
        </div>
      )}
      {loaded.state === "loaded" && loaded.waiting.count === 0 && (
        <p className="rounded-lg border p-4 text-sm text-muted-foreground">
          Nothing is waiting for a person in the {RECENT_CONVERSATIONS} most recent conversations.
        </p>
      )}
      {loaded.state === "loaded" && loaded.waiting.count > 0 && (
        <>
          <ul className="divide-y rounded-lg border">
            {loaded.waiting.first.map((conversation) => {
              const who = conversationWith(conversation);
              return (
                <li key={conversation.conversation_id}>
                  <Link
                    to={conversationHref(conversation.conversation_id)}
                    className="flex items-center gap-3 px-4 py-3 hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
                  >
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium">
                        {conversation.preview || "Empty conversation"}
                      </span>
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
          <p className="text-xs text-muted-foreground">
            Counted among the {RECENT_CONVERSATIONS} most recent conversations.{" "}
            <Link to="/team/chat" className="underline underline-offset-4">
              Open the chat
            </Link>{" "}
            to see them all.
          </p>
        </>
      )}
    </section>
  );
}

export default function TeamHome() {
  const admin = useTeamAdmin();
  return (
    // The team layout's SidebarInset is the page's <main>.
    <div className="mx-auto w-full max-w-3xl space-y-9 px-4 py-8 sm:px-6 sm:py-10">
      <h1 className="text-2xl font-semibold tracking-tight">Sernia Capital team</h1>
      <WaitingSection />
      <section aria-labelledby="pages-title" className="space-y-3">
        <h2 id="pages-title" className="text-lg font-semibold">
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
                <span>
                  <span className="block text-sm font-medium">{section.title}</span>
                  <span className="mt-1 block text-sm text-muted-foreground">{section.description}</span>
                </span>
              </Link>
            </li>
          ))}
        </ul>
        <p className="text-sm">
          <Link to="/team/leads" className="underline underline-offset-4">
            How it works
          </Link>
          <span className="text-muted-foreground">: how leads become replies and showings, and who is told what.</span>
        </p>
      </section>
    </div>
  );
}
