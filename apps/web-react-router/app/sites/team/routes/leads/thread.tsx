import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@clerk/react-router";
import { Link, useNavigate } from "react-router";
import {
  ArrowLeft,
  Bot,
  CalendarDays,
  Check,
  Copy,
  ExternalLink,
  Loader2,
  Mail,
  RefreshCw,
} from "lucide-react";

import type { Route } from "./+types/thread";
import { Button } from "~/components/ui/button";
import { Skeleton } from "~/components/ui/skeleton";
import { Textarea } from "~/components/ui/textarea";
import { cn } from "~/lib/utils";
import { ToolApprovalCard, type PendingApproval } from "~/sites/team/components/chat/tool-cards";
import { OutdatedBadge, ShowingBadge, StatusBadge } from "~/sites/team/components/lead-badges";
import {
  type AgentRun,
  type BookingLink,
  LEADS_API,
  LEADS_PATH,
  type LeadEmail,
  type LeadShowing,
  type LeadThreadPage,
  type WaitingCard,
  bookedThrough,
  changeLine,
  factLabel,
  fullTime,
  orderedFacts,
  refusal,
  runAction,
  runResult,
  showingState,
  waitedFor,
} from "~/sites/team/lib/lead-inbox";
import { withHandOff } from "~/sites/team/lib/lead-hand-off";
import { teamTitle } from "~/sites/team/lib/team-meta";

export const meta = () => [{ title: teamTitle("Lead") }];

// One lead's page (/team/leads/threads/:threadId): who they are and what
// Zillow's form said, the thread's own booking link, what waits for the team,
// their showings, the emails in order and each run of the lead agent.
// Calendar events and notifications link here, so the path stays.

function useLeadsFetch() {
  const { getToken } = useAuth();
  return useCallback(
    async (url: string, fallback: string) => {
      const token = await getToken();
      const response = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
      const body = await response.json().catch(() => null);
      if (!response.ok) throw Object.assign(new Error(refusal(response.status, body, fallback)), { status: response.status });
      return body;
    },
    [getToken],
  );
}

function Section({ title, children, aside }: { title: string; children: React.ReactNode; aside?: React.ReactNode }) {
  const id = `lead-${title.toLowerCase().replaceAll(" ", "-")}`;
  return (
    <section aria-labelledby={id} className="space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 id={id} className="text-lg font-semibold">
          {title}
        </h3>
        {aside}
      </div>
      {children}
    </section>
  );
}

/**
 * What a person does from the header, in one row: open the thread in Gmail,
 * copy the lead's own booking link, hand the lead to Sernia AI. What each
 * did shows under the row.
 */
function LeadActions({
  gmailUrl,
  links,
  problem,
  handOffFrom,
  handedTo,
}: {
  gmailUrl: string;
  links: BookingLink[];
  problem: string | null;
  handOffFrom: string;
  handedTo: string | null;
}) {
  const fetchLeads = useLeadsFetch();
  const navigate = useNavigate();
  const [copied, setCopied] = useState<{ unit: string; url: string; failed: boolean } | null>(null);
  const [handing, setHanding] = useState(false);
  const [words, setWords] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const withLinks = links.filter((link): link is BookingLink & { url: string } => Boolean(link.url));
  const without = links.filter((link) => !link.url);

  async function copy(link: BookingLink & { url: string }) {
    try {
      await navigator.clipboard.writeText(link.url);
      setCopied({ unit: link.unit, url: link.url, failed: false });
    } catch {
      setCopied({ unit: link.unit, url: link.url, failed: true });
    }
  }

  async function handOff() {
    if (!words.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const { block } = await fetchLeads(
        `/api/sernia-ai/conversation/${encodeURIComponent(handOffFrom)}/hand-off`,
        "Could not hand this lead to Sernia AI",
      );
      // The chat page starts a new chat with these words, then the lead's context
      // (~/sites/team/lib/lead-hand-off.ts), and closes what waits here.
      navigate("/team/chat", { state: { handOff: { from: handOffFrom, text: withHandOff(block, words) } } });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not hand this lead to Sernia AI.");
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3 pt-1">
      <div className="flex flex-wrap items-center gap-2">
        <Button asChild variant="outline" size="sm" className="gap-1.5">
          <a href={gmailUrl} target="_blank" rel="noreferrer">
            <Mail className="size-3.5" aria-hidden="true" />
            Open in Gmail
            <ExternalLink className="size-3 opacity-60" aria-hidden="true" />
          </a>
        </Button>
        {withLinks.map((link) => (
          <Button key={link.unit} variant="outline" size="sm" className="gap-1.5" onClick={() => void copy(link)}>
            {copied?.unit === link.unit && !copied.failed ? (
              <Check className="size-3.5" aria-hidden="true" />
            ) : (
              <Copy className="size-3.5" aria-hidden="true" />
            )}
            {withLinks.length > 1 ? `Copy booking link: ${link.unit}` : "Copy booking link"}
          </Button>
        ))}
        <Button variant="outline" size="sm" className="gap-1.5" aria-expanded={handing} onClick={() => setHanding(!handing)}>
          <Bot className="size-3.5" aria-hidden="true" />
          Hand to Sernia AI
        </Button>
        {handedTo && (
          <Link to={`/team/chat?id=${encodeURIComponent(handedTo)}`} className="text-sm underline underline-offset-4">
            Open the chat it was handed to
          </Link>
        )}
      </div>

      {copied && (
        <div role="status" className="space-y-1 text-sm">
          <p className={copied.failed ? "text-amber-900 dark:text-amber-200" : "text-muted-foreground"}>
            {copied.failed
              ? `This browser did not allow copying. ${copied.unit}'s link for this lead:`
              : `Copied ${copied.unit}'s link for this lead. A booking made through it shows on this page.`}
          </p>
          <input
            readOnly
            value={copied.url}
            aria-label={`Booking link for ${copied.unit}`}
            // When copying was refused, select it at once for the person to copy.
            autoFocus={copied.failed}
            onFocus={(event) => event.currentTarget.select()}
            className="w-full rounded-md border bg-muted/40 px-2 py-1 font-mono text-xs"
          />
        </div>
      )}
      {problem && <p className="text-sm text-muted-foreground">{problem}</p>}
      {!problem && links.length === 0 && (
        <p className="text-sm text-muted-foreground">No booking link: no unit is on offer.</p>
      )}
      {without.map((link) => (
        <p key={link.unit} className="text-sm text-muted-foreground">
          No booking link for {link.unit}: {link.no_link_because}
        </p>
      ))}

      {handing && (
        <form
          className="space-y-2 rounded-lg border p-3"
          onSubmit={(event) => {
            event.preventDefault();
            void handOff();
          }}
        >
          <label htmlFor="hand-off-words" className="text-sm font-medium">
            What should Sernia AI do?
          </label>
          <Textarea
            id="hand-off-words"
            value={words}
            onChange={(event) => setWords(event.target.value)}
            placeholder="For example: reply that the unit is still available and offer Saturday morning."
            rows={3}
            disabled={busy}
          />
          <p className="text-xs text-muted-foreground">
            Opens a new Sernia AI chat with your words, the lead&rsquo;s emails and the agent&rsquo;s case. Anything
            waiting here is closed, and Sernia AI asks before it sends anything.
          </p>
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
          <Button type="submit" size="sm" disabled={busy || !words.trim()} className="gap-1.5">
            {busy && <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />}
            {busy ? "Opening Sernia AI…" : "Hand to Sernia AI"}
          </Button>
        </form>
      )}
    </div>
  );
}

function Notes({ notes }: { notes: unknown }) {
  const list = Array.isArray(notes) ? notes.filter((note) => typeof note === "string" && note) : [];
  if (list.length === 0) return null;
  return (
    <div className="text-sm">
      <p className="font-medium">Team notes</p>
      <ul className="mt-1 list-disc space-y-1 pl-5 [overflow-wrap:anywhere]">
        {list.map((note, index) => (
          <li key={index}>{note}</li>
        ))}
      </ul>
    </div>
  );
}

/**
 * A card waiting on the team, decided here with the chat's own approval card
 * (Approve, Edit in the full-screen editor, Reject; or Mark handled). When the
 * thread's showings changed after the draft was written, that comes first.
 */
function WaitingItem({
  card,
  now,
  onDecided,
}: {
  card: WaitingCard;
  now: Date;
  onDecided: (result: { output?: string; status?: string }) => void;
}) {
  const { getToken } = useAuth();
  const changes = card.showing_changes ?? [];
  const pending: PendingApproval = { toolCallId: card.tool_call_id, toolName: card.tool_name, args: card.args };
  return (
    <div className="space-y-3">
      <p className="text-xs text-muted-foreground">
        {card.kind === "draft" ? "Drafted" : "Asked"} {fullTime(card.since)} · waiting {waitedFor(card.since, now)} ·{" "}
        <Link to={card.chat_path} className="underline underline-offset-4">
          Raw conversation
        </Link>
      </p>
      {changes.length > 0 && (
        <div
          role="alert"
          className="space-y-1 rounded-lg border-2 border-red-400 bg-red-50 p-3 text-sm text-red-950 dark:border-red-800 dark:bg-red-950/40 dark:text-red-100"
        >
          {changes.map((change, index) => (
            <p key={index} className="font-medium">
              {changeLine(change)}
            </p>
          ))}
          <p>Check the draft still fits before you approve it: edit it, or reject it.</p>
        </div>
      )}
      {card.args.why && <p className="text-sm text-muted-foreground [overflow-wrap:anywhere]">Why: {card.args.why}</p>}
      <Notes notes={card.args.team_notes} />
      <ToolApprovalCard
        pending={pending}
        allPending={[pending]}
        conversationId={card.conversation_id}
        apiBase="/api/sernia-ai"
        getToken={getToken}
        isProcessing={false}
        onApprovalComplete={onDecided}
      />
    </div>
  );
}

function ShowingItem({ showing }: { showing: LeadShowing }) {
  const upcoming = showing.status === "booked" && !showing.past;
  return (
    <li className="space-y-1 px-4 py-3 text-sm">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p>
          <span className="font-medium">{showing.unit}</span>{" "}
          <span className="tabular-nums">{fullTime(showing.starts_at)}</span>
        </p>
        <span
          className={cn(
            "text-xs font-medium",
            upcoming ? "text-emerald-700 dark:text-emerald-400" : "text-muted-foreground",
          )}
        >
          {showingState(showing)}
        </span>
      </div>
      <p className="text-muted-foreground">
        Booked by {showing.booked_by} {bookedThrough(showing.source)}
        {showing.booked_at && <>, {fullTime(showing.booked_at)}</>}
        {showing.status === "booked" &&
          (showing.details_added ? " · details added" : " · details not added yet: no text reminder or invite")}
        {showing.cancelled_at && <> · {fullTime(showing.cancelled_at)}</>}
      </p>
      {upcoming && (
        <Link to={showing.showings_path} className="inline-block underline underline-offset-4">
          Open in Showings
        </Link>
      )}
    </li>
  );
}

function EmailItem({ email, leadName }: { email: LeadEmail; leadName: string }) {
  return (
    <li
      className={cn(
        "rounded-lg border p-3 text-sm sm:p-4",
        email.from_sernia ? "bg-muted/50 sm:ml-10" : "bg-background sm:mr-10",
      )}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
        <p className="font-medium">{email.from_sernia ? "Sernia" : leadName}</p>
        <p className="text-xs text-muted-foreground">
          {fullTime(email.sent_at)}
          {email.sent_by === "inbox" && " · approved here"}
          {email.sent_by === "automatic" && " · sent automatically"}
        </p>
      </div>
      <p className="mt-2 whitespace-pre-wrap [overflow-wrap:anywhere]">{email.text || "(no message text)"}</p>
      {email.form.length > 0 && (
        <details className="mt-2 text-xs text-muted-foreground">
          <summary className="cursor-pointer select-none">Zillow form ({email.form.length})</summary>
          <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
            {email.form.map((fact) => (
              <div key={fact.label} className="contents">
                <dt>{fact.label}</dt>
                <dd className="[overflow-wrap:anywhere]">{fact.value}</dd>
              </div>
            ))}
          </dl>
        </details>
      )}
    </li>
  );
}

function RunItem({ run }: { run: AgentRun }) {
  return (
    <li className="space-y-1.5 px-4 py-3 text-sm">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p>
          <span className="font-medium">{runAction(run.action)}</span>
          <span className="text-muted-foreground"> · {runResult(run)}</span>
        </p>
        <time dateTime={run.at} className="text-xs text-muted-foreground">
          {fullTime(run.at)}
        </time>
      </div>
      {run.reason && <p className="text-muted-foreground [overflow-wrap:anywhere]">{run.reason}</p>}
      {run.question && <p className="[overflow-wrap:anywhere]">Asked: {run.question}</p>}
      <Notes notes={run.team_notes} />
      <p className="text-xs text-muted-foreground">
        Read {run.emails_seen} {run.emails_seen === 1 ? "email" : "emails"} ·{" "}
        <Link to={run.chat_path} className="underline underline-offset-4">
          Raw conversation
        </Link>
        {run.handed_to && (
          <>
            {" · "}
            <Link to={`/team/chat?id=${encodeURIComponent(run.handed_to)}`} className="underline underline-offset-4">
              Sernia AI chat
            </Link>
          </>
        )}
      </p>
    </li>
  );
}

function LoadingPage() {
  return (
    <div role="status" aria-label="Loading the lead" className="space-y-4">
      <Skeleton className="h-8 w-56" />
      <Skeleton className="h-4 w-80 max-w-full" />
      <div className="flex gap-2">
        <Skeleton className="h-8 w-32" />
        <Skeleton className="h-8 w-40" />
      </div>
      <Skeleton className="h-40 w-full" />
    </div>
  );
}

export default function LeadThread({ params }: Route.ComponentProps) {
  const fetchLeads = useLeadsFetch();
  const threadId = params.threadId;
  const [page, setPage] = useState<LeadThreadPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<{ message: string; missing: boolean } | null>(null);
  const [now, setNow] = useState(() => new Date());

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setPage(await fetchLeads(`${LEADS_API}/${encodeURIComponent(threadId)}`, "Could not load this lead"));
      setNow(new Date());
    } catch (err) {
      const status = (err as { status?: number }).status;
      setError({ message: err instanceof Error ? err.message : "Could not load this lead.", missing: status === 404 });
    } finally {
      setLoading(false);
    }
  }, [fetchLeads, threadId]);

  useEffect(() => {
    setPage(null);
    void load();
  }, [load]);

  useEffect(() => {
    const again = () => {
      if (document.visibilityState === "visible") void load();
    };
    document.addEventListener("visibilitychange", again);
    return () => document.removeEventListener("visibilitychange", again);
  }, [load]);

  // What the last decision here did, from the API's answer: sent, rejected, or held with why.
  const [decision, setDecision] = useState<string | null>(null);
  const decided = useCallback(
    (result: { output?: string; status?: string }) => {
      setDecision(result.output || (result.status === "pending_approval" ? "Not sent: see the card." : "Done."));
      void load();
    },
    [load],
  );

  const back = (
    <Link to={LEADS_PATH} className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
      <ArrowLeft className="size-3.5" aria-hidden="true" />
      Leads
    </Link>
  );

  if (!page) {
    return (
      <div className="space-y-4">
        {back}
        {error ? (
          <div role="alert" className="rounded-lg border border-destructive/50 p-3 text-sm">
            <p>{error.message}</p>
            {!error.missing && (
              <Button variant="outline" size="sm" className="mt-2" disabled={loading} onClick={() => void load()}>
                Try again
              </Button>
            )}
          </div>
        ) : (
          <LoadingPage />
        )}
      </div>
    );
  }

  const { thread } = page;
  const facts = orderedFacts(page.facts);
  const handOffFrom = page.waiting[0]?.conversation_id ?? page.latest_conversation_id;
  return (
    <article className="max-w-4xl space-y-8">
      <header className="space-y-3">
        <div className="flex items-center justify-between gap-2">
          {back}
          <Button variant="ghost" size="sm" className="gap-1.5" disabled={loading} onClick={() => void load()}>
            <RefreshCw className={cn("size-3.5", loading && "animate-spin")} aria-hidden="true" />
            Refresh
          </Button>
        </div>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <h2 className="text-2xl font-semibold">{thread.name}</h2>
          <StatusBadge status={thread.status} waitingSince={thread.waiting_since} now={now} />
          {thread.draft_outdated && <OutdatedBadge />}
          {thread.showing && <ShowingBadge showing={thread.showing} />}
        </div>
        <p className="text-sm">
          {thread.unit && <span className="font-medium">{thread.unit}</span>}
          {thread.unit && thread.address && " · "}
          {thread.address}
        </p>
        <p className="text-xs text-muted-foreground [overflow-wrap:anywhere]">
          {thread.subject} · {thread.email_count} {thread.email_count === 1 ? "email" : "emails"}
        </p>
        {(facts.length > 0 || page.phone) && (
          <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-4">
            {facts.map((fact) => (
              <div key={fact.label}>
                <dt className="text-xs text-muted-foreground">{factLabel(fact.label)}</dt>
                <dd className="[overflow-wrap:anywhere]">{fact.value}</dd>
              </div>
            ))}
            {page.phone && (
              <div>
                <dt className="text-xs text-muted-foreground">Phone</dt>
                <dd>
                  <a href={`tel:${page.phone}`} className="underline underline-offset-4">
                    {page.phone}
                  </a>
                </dd>
              </div>
            )}
          </dl>
        )}
        <LeadActions
          gmailUrl={page.gmail_url}
          links={page.booking_links}
          problem={page.booking_links_problem}
          handOffFrom={handOffFrom}
          handedTo={page.handed_to}
        />
      </header>

      {decision && (
        <p role="status" className="rounded-lg border bg-muted/40 p-3 text-sm [overflow-wrap:anywhere]">
          {decision}
        </p>
      )}

      {page.waiting.length > 0 && (
        <Section title="Waiting for the team">
          <div className="space-y-6">
            {page.waiting.map((card) => (
              // Keyed by what the card says: a reload that changes nothing keeps an edit in progress.
              <WaitingItem
                key={`${card.tool_call_id}-${JSON.stringify(card.args).length}`}
                card={card}
                now={now}
                onDecided={decided}
              />
            ))}
          </div>
        </Section>
      )}

      <Section title="Showings">
        {page.showings.length === 0 ? (
          <p className="flex items-center gap-2 text-sm text-muted-foreground">
            <CalendarDays className="size-4" aria-hidden="true" />
            No showing booked through this lead&rsquo;s link.
          </p>
        ) : (
          <ul className="divide-y rounded-lg border">
            {page.showings.map((showing) => (
              <ShowingItem key={showing.attendee_id} showing={showing} />
            ))}
          </ul>
        )}
      </Section>

      <Section
        title="Emails"
        aside={
          page.emails_read_at && (
            <p className="text-xs text-muted-foreground">
              {page.emails_live
                ? "Read from Gmail just now"
                : `As the lead agent read them, ${fullTime(page.emails_read_at)}`}
            </p>
          )
        }
      >
        <ol className="space-y-3">
          {page.emails.map((email, index) => (
            <EmailItem key={`${email.sent_at}-${index}`} email={email} leadName={thread.first_name} />
          ))}
        </ol>
        <p className="text-xs text-muted-foreground">
          {page.emails_checked
            ? "Checked against Gmail just now. "
            : "Gmail could not be checked just now, so a reply written there may be missing. "}
          <a href={page.gmail_url} target="_blank" rel="noreferrer" className="underline underline-offset-4">
            Open the thread in Gmail
          </a>
          .
        </p>
      </Section>

      <Section title="Lead agent activity">
        <ol className="divide-y rounded-lg border">
          {page.runs.map((run) => (
            <RunItem key={run.conversation_id} run={run} />
          ))}
        </ol>
      </Section>
    </article>
  );
}
