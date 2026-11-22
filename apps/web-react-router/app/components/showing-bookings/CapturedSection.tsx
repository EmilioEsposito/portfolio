import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, RefreshCw, Send } from "lucide-react";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import {
  type CapturedNotice,
  type CapturedState,
  type SendCapturedReport,
  type SendOutcome,
  type SendResult,
  type SettingsView,
  CAPTURED_STATE_LABELS,
  CHANNEL_LABELS,
  capturedLine,
  describeSendResult,
  formatWhen,
  pickedNotices,
  sendQuestion,
  summarizeSend,
} from "~/lib/showing-bookings";
import { cn } from "~/lib/utils";
import { useShowingBookingsApi } from "./use-showing-bookings-api";

const STATE_STYLES: Record<CapturedState, string> = {
  ready: "border-sky-300 text-sky-800 dark:border-sky-800 dark:text-sky-300",
  too_late: "border-muted-foreground/40 text-muted-foreground",
  no_longer_applies: "border-muted-foreground/40 text-muted-foreground",
};

const OUTCOME_STYLES: Record<SendOutcome, string> = {
  sent: "text-green-800 dark:text-green-400",
  waiting: "text-sky-800 dark:text-sky-300",
  unknown: "text-amber-800 dark:text-amber-300",
  not_sent: "text-red-700 dark:text-red-400",
  kept: "text-muted-foreground",
  handled: "text-muted-foreground",
};

// A message longer than this starts folded.
const LONG_TEXT = 160;
// One request sends this many; the server takes up to 200.
const SEND_AT_ONCE = 50;

/** A message's words, folded while they are long. */
function Words({ id, text, label }: { id: string; text: string; label: string }) {
  const [open, setOpen] = useState(false);
  const long = text.length > LONG_TEXT || text.split("\n").length > 3;
  return (
    <div className="mt-2">
      <div className="rounded-md bg-muted/60 p-2.5">
        <p
          id={id}
          className={cn("whitespace-pre-wrap break-words text-sm leading-6", long && !open && "line-clamp-3")}
        >
          {text}
        </p>
      </div>
      {long && (
        <Button
          type="button"
          variant="link"
          size="sm"
          className="h-auto px-0 py-1"
          aria-expanded={open}
          aria-controls={id}
          aria-label={`${open ? "Show less of" : "Show all of"} ${label}`}
          onClick={() => setOpen(!open)}
        >
          {open ? "Show less" : "Show all"}
        </Button>
      )}
    </div>
  );
}

function CapturedItem({
  notice,
  picked,
  disabled,
  onTick,
}: {
  notice: CapturedNotice;
  picked: ReadonlySet<number>;
  disabled: boolean;
  onTick: (ticked: boolean) => void;
}) {
  const ready = notice.state === "ready";
  const line = capturedLine(notice, picked);
  const id = `captured-${notice.id}`;
  const label = `${notice.kind_label} to ${notice.to}`;
  return (
    <li className={cn("rounded-lg border p-3 sm:p-4", !ready && "bg-muted/30")}>
      <div className="flex items-start gap-3">
        <input
          id={id}
          type="checkbox"
          checked={ready && picked.has(notice.id)}
          disabled={!ready || disabled}
          aria-describedby={line ? `${id}-line` : undefined}
          onChange={(event) => onTick(event.target.checked)}
          className="mt-1 size-4 shrink-0 accent-foreground"
        />
        <div className={cn("min-w-0 flex-1", !ready && "text-muted-foreground")}>
          <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-1">
            <label htmlFor={id} className={cn("min-w-0 text-sm", ready && !disabled && "cursor-pointer")}>
              <span className="font-medium">{notice.kind_label}</span>
              <span className="mt-0.5 block break-words text-muted-foreground">
                {CHANNEL_LABELS[notice.channel]} to {notice.to}
              </span>
              <span className="block text-muted-foreground">
                Unit {notice.unit} · showing {formatWhen(notice.showing_starts_at)}
              </span>
            </label>
            <Badge variant="outline" className={cn("shrink-0 font-medium", STATE_STYLES[notice.state])}>
              {CAPTURED_STATE_LABELS[notice.state]}
            </Badge>
          </div>
          {notice.conversation_id && (
            <p className="mt-1 text-sm">
              <a
                href={`/sernia-chat?id=${encodeURIComponent(notice.conversation_id)}`}
                className="underline underline-offset-4"
              >
                Conversation
              </a>
            </p>
          )}
          <p id={`${id}-line`} className={cn("text-sm", line && "mt-1.5", ready ? "font-medium" : "")}>
            {line}
          </p>
          <Words id={`${id}-words`} text={notice.wording} label={label} />
        </div>
      </div>
    </li>
  );
}

function Results({ results }: { results: SendResult[] }) {
  return (
    <ul className="mt-2 space-y-1.5 text-sm" aria-label="What happened to each message">
      {results.map((result) => (
        <li key={result.id} className="rounded-md bg-muted/40 px-3 py-2">
          <span className="font-medium">{result.kind_label}</span>
          {result.to && <span className="break-words text-muted-foreground"> to {result.to}</span>}
          {result.showing_starts_at && (
            <span className="text-muted-foreground">
              {" "}
              · {result.unit} {formatWhen(result.showing_starts_at)}
            </span>
          )}
          <span className={cn("mt-0.5 block", OUTCOME_STYLES[result.outcome])}>{describeSendResult(result)}</span>
        </li>
      ))}
    </ul>
  );
}

/** The captured messages, and sending the ticked ones by hand. */
export function CapturedSection({
  view,
  changes,
  onChanged,
}: {
  view: SettingsView | null;
  /** Counts what changed elsewhere on the tab, so the list is read again. */
  changes: number;
  /** Something was sent, so the Messages list is out of date. This one reads itself again. */
  onChanged: () => void;
}) {
  const api = useShowingBookingsApi();
  const [notices, setNotices] = useState<CapturedNotice[] | null>(null);
  // The ready ones a person unticked: everything else ready is ticked, new ones too.
  const [unticked, setUnticked] = useState<ReadonlySet<number>>(new Set());
  const [loading, setLoading] = useState(true);
  // The ones the question was asked about, which are the ones sent on a yes.
  const [asking, setAsking] = useState<CapturedNotice[] | null>(null);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Kept apart from a failed read, so reading the list again does not clear it.
  const [sendError, setSendError] = useState<string | null>(null);
  const [results, setResults] = useState<SendResult[] | null>(null);
  const confirmRef = useRef<HTMLDivElement>(null);
  const sendRef = useRef<HTMLButtonElement>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    const result = await api<CapturedNotice[]>("/notices/captured", {}, "Could not load the captured messages");
    if (result.ok) setNotices(result.body);
    else setError(result.message);
    setLoading(false);
  }, [api]);

  useEffect(() => {
    void load();
  }, [load, changes]);

  useEffect(() => {
    if (asking) confirmRef.current?.focus();
  }, [asking]);

  const picked = asking ?? (notices ? pickedNotices(notices, unticked) : []);
  const pickedIds = new Set(picked.map((notice) => notice.id));
  const ready = notices?.filter((notice) => notice.state === "ready").length ?? 0;

  function close() {
    setAsking(null);
    // The button is disabled while the question is open.
    requestAnimationFrame(() => sendRef.current?.focus());
  }

  async function send() {
    const ids = picked.map((notice) => notice.id);
    const done: SendResult[] = [];
    let failed: string | null = null;
    setSending(true);
    setSendError(null);
    for (let start = 0; start < ids.length && failed === null; start += SEND_AT_ONCE) {
      const result = await api<SendCapturedReport>(
        "/notices/send-captured",
        { method: "POST", body: JSON.stringify({ ids: ids.slice(start, start + SEND_AT_ONCE) }) },
        "Could not send",
      );
      if (result.ok) done.push(...result.body.results);
      else failed = result.message;
    }
    // Read again before the question closes: some may have been handled even when
    // the answer was lost.
    await load();
    setResults(done.length ? done : null);
    setSendError(failed);
    setSending(false);
    setAsking(null);
    onChanged();
  }

  return (
    <section aria-labelledby="captured-heading" className="rounded-xl border p-5 sm:p-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0 sm:flex-1">
          <h3 id="captured-heading" className="text-xl font-semibold">
            Captured messages{notices ? ` (${ready})` : ""}
          </h3>
          <p className="mt-1 text-sm leading-6 text-muted-foreground">Untick any you don&rsquo;t want to send.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            size="sm"
            className="gap-2"
            disabled={loading || sending || asking !== null}
            onClick={() => void load()}
            aria-label="Reload captured messages"
          >
            <RefreshCw className={cn("size-4", loading && "animate-spin")} aria-hidden="true" />
            Reload
          </Button>
          <Button
            ref={sendRef}
            size="sm"
            className="gap-2"
            disabled={picked.length === 0 || asking !== null || loading || !view}
            aria-expanded={asking !== null}
            onClick={() => {
              setResults(null);
              setSendError(null);
              setAsking(picked);
            }}
          >
            <Send className="size-4" aria-hidden="true" />
            Send captured messages now
          </Button>
        </div>
      </div>

      {asking && view && (
        <div
          ref={confirmRef}
          tabIndex={-1}
          role="group"
          aria-labelledby="captured-question"
          className="mt-4 rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900 outline-none focus-visible:ring-2 focus-visible:ring-ring dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200"
          onKeyDown={(event) => {
            if (event.key === "Escape" && !sending) close();
          }}
        >
          <p id="captured-question" className="leading-6">
            {sendQuestion(picked, view.is_production)}
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <Button size="sm" className="gap-2" disabled={sending} onClick={() => void send()}>
              {sending && <Loader2 className="size-4 animate-spin" aria-hidden="true" />}
              {picked.length === 1 ? "Send 1 message" : `Send ${picked.length} messages`}
            </Button>
            <Button variant="outline" size="sm" className="text-foreground" disabled={sending} onClick={close}>
              Not now
            </Button>
          </div>
        </div>
      )}

      <div role="status" aria-live="polite" className={cn(results && "mt-4")}>
        {results && (
          <>
            <p className="text-sm font-medium">{summarizeSend(results)}</p>
            <Results results={results} />
          </>
        )}
      </div>
      {sendError && (
        <p role="alert" className="mt-4 rounded-lg border border-destructive/50 p-3 text-sm">
          {sendError} {results ? "The messages after these were not sent." : "Check the list before sending again."}
        </p>
      )}
      {error && (
        <p role="alert" className="mt-4 rounded-lg border border-destructive/50 p-3 text-sm">
          {error}
        </p>
      )}
      {loading && notices === null && (
        <p role="status" className="mt-4 flex items-center gap-2 text-sm">
          <Loader2 className="size-4 animate-spin" aria-hidden="true" />
          Loading captured messages…
        </p>
      )}
      {notices !== null && notices.length === 0 && (
        <p className="mt-4 rounded-lg border border-dashed p-4 text-sm leading-6 text-muted-foreground">
          No captured messages.
        </p>
      )}
      {notices !== null && notices.length > 0 && (
        <ul className="mt-4 space-y-2.5" aria-label="Captured messages">
          {notices.map((notice) => (
            <CapturedItem
              key={notice.id}
              notice={notice}
              picked={pickedIds}
              disabled={asking !== null}
              onTick={(ticked) =>
                setUnticked((current) => {
                  const next = new Set(current);
                  if (ticked) next.delete(notice.id);
                  else next.add(notice.id);
                  return next;
                })
              }
            />
          ))}
        </ul>
      )}
    </section>
  );
}
