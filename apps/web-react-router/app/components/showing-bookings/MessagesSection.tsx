import { useCallback, useEffect, useState } from "react";
import { Loader2, RefreshCw, Send } from "lucide-react";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import {
  type DrainReport,
  type Notice,
  CHANNEL_LABELS,
  MODE_LABELS,
  STATUS_LABELS,
  describeDrain,
  describeGuard,
  formatWhen,
  humanize,
  needsAttention,
} from "~/lib/showing-bookings";
import { cn } from "~/lib/utils";
import { useShowingBookingsApi } from "./use-showing-bookings-api";

const STATUS_STYLES: Record<Notice["status"], string> = {
  pending: "border-sky-300 text-sky-800 dark:border-sky-800 dark:text-sky-300",
  held: "border-sky-300 text-sky-800 dark:border-sky-800 dark:text-sky-300",
  captured: "border-muted-foreground/40 text-muted-foreground",
  sent: "border-green-300 text-green-800 dark:border-green-900 dark:text-green-400",
  failed: "border-red-300 text-red-700 dark:border-red-900 dark:text-red-400",
  unknown: "border-amber-300 text-amber-800 dark:border-amber-900 dark:text-amber-300",
  skipped: "border-muted-foreground/40 text-muted-foreground",
  cancelled: "border-muted-foreground/40 text-muted-foreground",
};

function NoticeItem({ notice }: { notice: Notice }) {
  const details = [
    notice.mode && `Mode: ${MODE_LABELS[notice.mode]}`,
    // "Send mode off" or "capture" would only repeat the mode.
    !notice.guard_reason?.startsWith("send_mode_") && describeGuard(notice.guard_reason),
    notice.attempts > 0 && `Attempts: ${notice.attempts}`,
    notice.provider_status && `Provider: ${notice.provider_status}`,
  ].filter(Boolean);
  return (
    <li
      className={cn(
        "rounded-lg border p-4",
        needsAttention(notice.status) && "border-red-300 dark:border-red-900",
      )}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="font-medium">
            {CHANNEL_LABELS[notice.channel]} · {humanize(notice.kind)}
          </p>
          <p className="mt-0.5 break-words text-sm text-muted-foreground">
            Unit {notice.unit} · showing {formatWhen(notice.showing_starts_at)} · to {notice.recipient}
          </p>
        </div>
        <Badge variant="outline" className={cn("shrink-0 font-medium", STATUS_STYLES[notice.status])}>
          {STATUS_LABELS[notice.status]}
        </Badge>
      </div>
      <p className="mt-3 whitespace-pre-wrap break-words rounded-md bg-muted/60 p-3 text-sm leading-6">{notice.wording}</p>
      {notice.last_error && (
        <p className="mt-2 break-words text-sm text-red-700 dark:text-red-400">{notice.last_error}</p>
      )}
      <p className="mt-2 text-xs leading-5 text-muted-foreground">
        {notice.status === "pending"
          ? `Due ${formatWhen(notice.not_before)}`
          : notice.status === "held"
            ? "Waits for the confirmation text to be delivered"
            : notice.status === "cancelled"
              ? "Not sent"
              : `${STATUS_LABELS[notice.status]} ${formatWhen(notice.delivered_at)}`}
        {details.length > 0 && ` · ${details.join(" · ")}`}
      </p>
    </li>
  );
}

export function MessagesSection() {
  const api = useShowingBookingsApi();
  const [notices, setNotices] = useState<Notice[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastRun, setLastRun] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    const result = await api<Notice[]>("/notices", {}, "Could not load messages");
    if (result.ok) setNotices(result.body);
    else setError(result.message);
    setLoading(false);
  }, [api]);

  useEffect(() => {
    void load();
  }, [load]);

  async function sendPending() {
    setSending(true);
    setError(null);
    setLastRun(null);
    const result = await api<DrainReport>("/notices/send-pending", { method: "POST" }, "Could not run the sending step");
    if (result.ok) {
      setLastRun(describeDrain(result.body));
      await load();
    } else {
      setError(result.message);
    }
    setSending(false);
  }

  const pending = notices?.filter((notice) => notice.status === "pending").length ?? 0;

  return (
    <section aria-labelledby="messages-heading" className="rounded-xl border p-5 sm:p-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0 sm:flex-1">
          <h3 id="messages-heading" className="text-xl font-semibold">
            Messages
          </h3>
          <p className="mt-1 text-sm leading-6 text-muted-foreground">
            Every text, calendar event and invite a booking owes, with its wording and what happened to it. Newest first.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" className="gap-2" disabled={loading || sending} onClick={() => void load()}>
            <RefreshCw className={cn("size-4", loading && "animate-spin")} aria-hidden="true" />
            Reload
          </Button>
          <Button size="sm" className="gap-2" disabled={sending || loading} onClick={() => void sendPending()}>
            {sending ? <Loader2 className="size-4 animate-spin" aria-hidden="true" /> : <Send className="size-4" aria-hidden="true" />}
            Send pending now{pending > 0 ? ` (${pending})` : ""}
          </Button>
        </div>
      </div>
      <p className="mt-3 text-xs leading-5 text-muted-foreground">
        Send pending now releases or stops the messages held for a confirmation text, then runs the sending step once,
        as the job every minute does. Each message still goes through the send mode above.
      </p>
      <p role="status" aria-live="polite" className="mt-2 text-sm">
        {lastRun && `Last run: ${lastRun}`}
      </p>
      {error && (
        <p role="alert" className="mt-3 rounded-lg border border-destructive/50 p-3 text-sm">
          {error}
        </p>
      )}
      {loading && notices === null && (
        <p role="status" className="mt-4 flex items-center gap-2 text-sm">
          <Loader2 className="size-4 animate-spin" aria-hidden="true" />
          Loading messages…
        </p>
      )}
      {notices !== null && notices.length === 0 && (
        <p className="mt-4 rounded-lg border border-dashed p-4 text-sm leading-6 text-muted-foreground">
          No messages yet. Bookings will add them here once booking is built.
        </p>
      )}
      {notices !== null && notices.length > 0 && (
        <ul className="mt-4 space-y-3">
          {notices.map((notice) => (
            <NoticeItem key={notice.id} notice={notice} />
          ))}
        </ul>
      )}
    </section>
  );
}
