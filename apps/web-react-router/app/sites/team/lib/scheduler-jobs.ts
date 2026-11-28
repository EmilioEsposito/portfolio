// What the Scheduler page (/team/scheduler) shows for each job listed by
// GET /api/schedulers/get_jobs: its schedule in words, its next run both as
// "in 23 s" and as a clock time, and whether the list has fallen behind.

export type SchedulerJob = {
  id: string;
  /** What a person calls it; the scheduler's own name, or the id, when the job has no description. */
  name: string;
  /** One sentence, or null for a job registered without a description. */
  summary: string | null;
  description: string | null;
  service: "dbos" | "apscheduler";
  func_ref: string;
  args: unknown[];
  kwargs: Record<string, unknown>;
  /** APScheduler's own text: "interval[0:00:30]", "cron[hour='8,17', minute='0']", "date[…]". */
  trigger: string;
  /** The trigger's time zone ("America/New_York"); a cron trigger's text leaves it out. */
  timezone: string | null;
  next_run_time: string | null;
  // Null while the job is pending (added before the scheduler started).
  coalesce: boolean | null;
  executor: string | null;
  max_instances: number | null;
  misfire_grace_time: number | null;
  pending: boolean;
  /**
   * Runs when work in its table is due, not on a schedule of its own (api/src/apscheduler_service/wakeups.py).
   * Listed with no next run while nothing waits. Absent from an API older than this field.
   */
  wake_up?: boolean;
};

/** The time zone the team reads clock times in, as the other team pages do. */
export const TEAM_TIME_ZONE = "America/New_York";
/** How often the page reloads the list while it is visible. */
export const REFRESH_EVERY_MS = 15_000;
/** A list older than this says so: about three refreshes have been missed. */
export const STALE_AFTER_MS = 45_000;
/** A next run this long before the list was loaded means the scheduler is behind. */
export const OVERDUE_AFTER_MS = 30_000;

export type Trigger =
  | { kind: "interval"; seconds: number }
  | { kind: "cron"; fields: Record<string, string> }
  | { kind: "date" }
  | { kind: "other" };

/** Read APScheduler's trigger text. An interval is Python's timedelta: "0:00:30" is 30 seconds. */
export function parseTrigger(trigger: string): Trigger {
  const interval = /^interval\[(?:(\d+) days?, )?(\d+):(\d{2}):(\d{2}(?:\.\d+)?)\]$/.exec(trigger);
  if (interval) {
    const [, days, hours, minutes, seconds] = interval;
    return {
      kind: "interval",
      seconds: Number(days ?? 0) * 86_400 + Number(hours) * 3_600 + Number(minutes) * 60 + Number(seconds),
    };
  }
  const cron = /^cron\[(.*)\]$/.exec(trigger);
  if (cron) {
    const fields: Record<string, string> = {};
    for (const match of cron[1].matchAll(/(\w+)='([^']*)'/g)) fields[match[1]] = match[2];
    return { kind: "cron", fields };
  }
  if (/^date\[.*\]$/.test(trigger)) return { kind: "date" };
  return { kind: "other" };
}

const ZONE_NAMES: Record<string, string> = {
  "America/New_York": "Eastern",
  "US/Eastern": "Eastern",
  "America/Chicago": "Central",
  "US/Central": "Central",
  "America/Denver": "Mountain",
  "US/Mountain": "Mountain",
  "America/Los_Angeles": "Pacific",
  "US/Pacific": "Pacific",
  UTC: "UTC",
  "Etc/UTC": "UTC",
  GMT: "UTC",
};

/** "America/New_York" reads "Eastern"; a zone not listed keeps its own name. */
export function zoneName(timeZone: string | null): string {
  if (!timeZone) return "";
  return ZONE_NAMES[timeZone] ?? timeZone;
}

function plural(count: number, one: string): string {
  return count === 1 ? one : `${count} ${one}s`;
}

/** 30 reads "Every 30 seconds"; 60 "Every minute"; 86400 "Every day". */
export function intervalWords(seconds: number): string | null {
  if (!(seconds > 0)) return null;
  if (seconds % 86_400 === 0) return `Every ${plural(seconds / 86_400, "day")}`;
  if (seconds % 3_600 === 0) return `Every ${plural(seconds / 3_600, "hour")}`;
  if (seconds % 60 === 0) return `Every ${plural(seconds / 60, "minute")}`;
  return `Every ${plural(seconds, "second")}`;
}

const DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const DAY_ABBREVIATIONS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];

function dayNumber(text: string): number | null {
  if (/^[0-6]$/.test(text)) return Number(text);
  const index = DAY_ABBREVIATIONS.indexOf(text.toLowerCase());
  return index === -1 ? null : index;
}

/** A cron list of whole numbers and ranges ("8,17", "8-10", "mon-fri"), or null for steps and the like. */
function expand(spec: string, read: (text: string) => number | null): number[] | null {
  const values = new Set<number>();
  for (const item of spec.split(",")) {
    const range = /^([^-]+)-([^-]+)$/.exec(item);
    const from = read(range ? range[1] : item);
    const to = range ? read(range[2]) : from;
    if (from === null || to === null || to < from) return null;
    for (let value = from; value <= to; value += 1) values.add(value);
  }
  return [...values].sort((a, b) => a - b);
}

function wholeNumber(max: number) {
  return (text: string): number | null => {
    if (!/^\d+$/.test(text)) return null;
    const value = Number(text);
    return value <= max ? value : null;
  };
}

function list(items: string[]): string {
  if (items.length <= 1) return items.join("");
  return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

/** Days 0 to 6, Monday first: "Every day", "Monday to Friday", "Saturday and Sunday". */
function daysWords(days: number[]): string {
  if (days.length === 7) return "Every day";
  if (days.length === 1) return `Every ${DAY_NAMES[days[0]]}`;
  const contiguous = days.every((day, index) => index === 0 || day === days[index - 1] + 1);
  if (contiguous && days.length >= 3) return `${DAY_NAMES[days[0]]} to ${DAY_NAMES[days[days.length - 1]]}`;
  return list(days.map((day) => DAY_NAMES[day]));
}

/** 17, 0 reads "5:00 pm". */
export function clockWords(hour: number, minute: number): string {
  const period = hour < 12 ? "am" : "pm";
  const twelve = hour % 12 === 0 ? 12 : hour % 12;
  return `${twelve}:${String(minute).padStart(2, "0")} ${period}`;
}

const MOST_TIMES = 12;

/** The cron triggers this app uses, in words; null for anything else (the page shows the raw text). */
export function cronWords(fields: Record<string, string>, timeZone: string | null): string | null {
  const given = (key: string) => fields[key] !== undefined && fields[key] !== "*";
  if (["year", "month", "day", "week"].some(given)) return null;
  if (given("second") && fields.second !== "0") return null;
  // APScheduler fills a field left out with "*" when a smaller field is given,
  // and with its lowest value otherwise: day_of_week='0-4' alone runs at 0:00.
  const stated = (key: string) => fields[key] !== undefined;
  const hourSpec =
    fields.hour ?? (stated("minute") || stated("second") ? "*" : given("day_of_week") ? "0" : "*");
  const minuteSpec = fields.minute ?? (stated("second") ? "*" : given("day_of_week") || given("hour") ? "0" : "*");
  const days = given("day_of_week") ? expand(fields.day_of_week, dayNumber) : [0, 1, 2, 3, 4, 5, 6];
  const minutes = minuteSpec === "*" ? null : expand(minuteSpec, wholeNumber(59));
  if (!days || !minutes) return null;
  const zone = zoneName(timeZone);
  const daysText = daysWords(days);

  if (hourSpec === "*") {
    if (minutes.length !== 1) return null;
    const hourly = minutes[0] === 0 ? "every hour, on the hour" : `every hour at :${String(minutes[0]).padStart(2, "0")}`;
    if (days.length === 7) return hourly.charAt(0).toUpperCase() + hourly.slice(1);
    return `${daysText}${zone ? ` (${zone})` : ""}, ${hourly}`;
  }
  const hours = expand(hourSpec, wholeNumber(23));
  if (!hours || hours.length * minutes.length > MOST_TIMES) return null;
  const times = hours.flatMap((hour) => minutes.map((minute) => clockWords(hour, minute)));
  return [`${daysText} at ${list(times)}`, zone].filter(Boolean).join(" ");
}

/** What a wake-up job says in place of a schedule. */
export const WAKE_UP_SCHEDULE = "Runs when work is due";
/** A wake-up job with nothing waiting. */
export const WAKE_UP_IDLE = "Idle: runs when work arrives";

/** The schedule in words, or null when the page should show the trigger's own text. */
export function scheduleWords(
  job: Pick<SchedulerJob, "trigger" | "timezone" | "next_run_time" | "wake_up">,
  now: Date,
): string | null {
  if (job.wake_up) return WAKE_UP_SCHEDULE;
  const trigger = parseTrigger(job.trigger);
  if (trigger.kind === "interval") return intervalWords(trigger.seconds);
  if (trigger.kind === "cron") return cronWords(trigger.fields, job.timezone);
  if (trigger.kind === "date") {
    const at = job.next_run_time ? new Date(job.next_run_time) : null;
    return at && !Number.isNaN(at.getTime()) ? `Once, ${clockText(at, now)}` : "Once";
  }
  return null;
}

/** 23,400 ms reads "23 s"; 4 minutes "4 min"; 2 h 5 min; 1 day 3 h. Rounds down. */
export function durationText(ms: number): string {
  const seconds = Math.floor(Math.abs(ms) / 1000);
  if (seconds < 60) return `${seconds} s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return minutes % 60 ? `${hours} h ${minutes % 60} min` : `${hours} h`;
  const days = Math.floor(hours / 24);
  const dayText = days === 1 ? "1 day" : `${days} days`;
  return hours % 24 ? `${dayText} ${hours % 24} h` : dayText;
}

/** "in 23 s" for a time ahead, "23 s ago" for one behind. */
export function relativeText(at: number, now: number): string {
  const ms = at - now;
  if (Math.abs(ms) < 1000) return ms >= 0 ? "now" : "just now";
  return ms > 0 ? `in ${durationText(ms)}` : `${durationText(ms)} ago`;
}

const PARTS = new Intl.DateTimeFormat("en-US", {
  timeZone: TEAM_TIME_ZONE,
  weekday: "short",
  year: "numeric",
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
  second: "2-digit",
  hour12: true,
});

function partsOf(date: Date): Record<string, string> {
  const parts: Record<string, string> = {};
  for (const part of PARTS.formatToParts(date)) parts[part.type] = part.value;
  return parts;
}

/**
 * A clock time in Eastern time, as the team reads it: "2:21:08 pm ET" today,
 * "Tue, Nov 24, 8:00 am ET" on another day. Seconds show only when not zero.
 */
export function clockText(at: Date, now: Date): string {
  const when = partsOf(at);
  const today = partsOf(now);
  const seconds = when.second && when.second !== "00" ? `:${when.second}` : "";
  const time = `${when.hour}:${when.minute}${seconds} ${(when.dayPeriod ?? "").toLowerCase()} ET`;
  const sameDay = when.year === today.year && when.month === today.month && when.day === today.day;
  if (sameDay) return time;
  const year = when.year === today.year ? "" : `, ${when.year}`;
  return `${when.weekday}, ${when.month} ${when.day}${year}, ${time}`;
}

export type NextRun =
  /** No next run: paused, or the scheduler has not started here. */
  | { kind: "none"; text: string }
  /** Ahead. `projected` when worked out from the schedule because the listed time has just passed. */
  | { kind: "upcoming"; at: number; projected: boolean }
  /** Passed since the list was loaded: the list has not caught up yet. */
  | { kind: "passed"; at: number }
  /** Already past when the list was loaded: the scheduler is behind or stopped. */
  | { kind: "overdue"; at: number };

/**
 * Where a job's next run stands now, given when the list was loaded.
 *
 * A job every few seconds passes its listed next run between refreshes. While
 * the list is fresh, an interval job's next run is worked out from its listed
 * one, since an interval always runs on the same beat. Anything else that has
 * passed is shown as passed, never as next.
 */
export function nextRun(
  job: Pick<SchedulerJob, "next_run_time" | "trigger" | "pending" | "wake_up">,
  loadedAt: number,
  now: number,
): NextRun {
  if (!job.next_run_time && job.wake_up && !job.pending) return { kind: "none", text: WAKE_UP_IDLE };
  if (!job.next_run_time) {
    return {
      kind: "none",
      text: job.pending ? "Not started: the scheduler is not running here" : "Paused: no next run",
    };
  }
  const at = Date.parse(job.next_run_time);
  if (Number.isNaN(at)) return { kind: "none", text: `Next run: ${job.next_run_time}` };
  if (at < loadedAt - OVERDUE_AFTER_MS) return { kind: "overdue", at };
  if (at > now) return { kind: "upcoming", at, projected: false };
  const trigger = parseTrigger(job.trigger);
  if (trigger.kind === "interval" && trigger.seconds > 0 && !isStale(loadedAt, now)) {
    const step = trigger.seconds * 1000;
    const ahead = at + (Math.floor((now - at) / step) + 1) * step;
    return { kind: "upcoming", at: ahead, projected: true };
  }
  return { kind: "passed", at };
}

/** Whether the list is too old to trust: about three refreshes missed. */
export function isStale(loadedAt: number, now: number): boolean {
  return now - loadedAt > STALE_AFTER_MS;
}

/** Whether the page should load the list again, counting from its last attempt. */
export function refreshDue(lastAttemptAt: number, now: number): boolean {
  return now - lastAttemptAt >= REFRESH_EVERY_MS;
}

/** The reason an API call failed, from FastAPI's `detail`, or the status. */
export function failureText(body: unknown, status: number): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => (item as { msg?: unknown })?.msg)
      .filter((message): message is string => typeof message === "string");
    if (messages.length) return messages.join("; ");
  }
  return `The server answered ${status}.`;
}
