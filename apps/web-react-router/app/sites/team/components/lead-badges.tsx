import { CalendarCheck } from "lucide-react";

import { Badge } from "~/components/ui/badge";
import { cn } from "~/lib/utils";
import {
  type LeadStatus,
  type ShowingSummary,
  STATUS_LABELS,
  showingLabel,
  waitedFor,
  waitsOnTeam,
} from "~/sites/team/lib/lead-inbox";

// The badges a lead thread carries on the leads list and on its page: where
// it stands with the team, and its showing.

const STATUS_STYLES: Record<LeadStatus, string> = {
  draft_waiting:
    "border-amber-400 bg-amber-100 text-amber-900 dark:border-amber-700 dark:bg-amber-950/50 dark:text-amber-200",
  needs_team: "border-rose-300 bg-rose-50 text-rose-900 dark:border-rose-800 dark:bg-rose-950/40 dark:text-rose-200",
  handed_off: "border-violet-300 text-violet-800 dark:border-violet-800 dark:text-violet-300",
  replied: "border-border text-muted-foreground",
  draft_rejected: "border-border text-muted-foreground",
  no_reply_needed: "border-border text-muted-foreground",
  handled: "border-border text-muted-foreground",
};

const SHOWING_STYLES: Record<ShowingSummary["state"], string> = {
  booked:
    "border-emerald-300 bg-emerald-50 text-emerald-900 dark:border-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-200",
  held: "border-border text-muted-foreground",
  cancelled: "border-border text-muted-foreground",
};

export function StatusBadge({
  status,
  waitingSince,
  now,
}: {
  status: LeadStatus;
  waitingSince?: string | null;
  now?: Date;
}) {
  return (
    <Badge variant="outline" className={cn("font-normal", STATUS_STYLES[status])}>
      {STATUS_LABELS[status]}
      {waitsOnTeam(status) && waitingSince && (
        <span className="ml-1 opacity-75">· {waitedFor(waitingSince, now)}</span>
      )}
    </Badge>
  );
}

export function ShowingBadge({ showing }: { showing: ShowingSummary }) {
  return (
    <Badge variant="outline" className={cn("gap-1 font-normal", SHOWING_STYLES[showing.state])}>
      {showing.state === "booked" && <CalendarCheck className="size-3" aria-hidden="true" />}
      {showingLabel(showing)}
    </Badge>
  );
}
