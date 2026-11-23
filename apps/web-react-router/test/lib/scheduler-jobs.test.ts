import assert from "node:assert/strict";
import test from "node:test";
import {
  clockText,
  cronWords,
  durationText,
  failureText,
  intervalWords,
  isStale,
  nextRun,
  OVERDUE_AFTER_MS,
  parseTrigger,
  REFRESH_EVERY_MS,
  refreshDue,
  relativeText,
  scheduleWords,
  STALE_AFTER_MS,
  zoneName,
} from "../../app/sites/team/lib/scheduler-jobs.ts";

const ET = "America/New_York";
// 2:21:00 pm Eastern on Monday, Nov 23, 2026 (EST, UTC-5).
const NOW = Date.parse("2026-11-23T19:21:00Z");

function job(trigger: string, timezone: string | null = ET, next_run_time: string | null = null) {
  return { trigger, timezone, next_run_time, pending: false };
}

test("an interval is Python's timedelta, hours:minutes:seconds", () => {
  assert.deepEqual(parseTrigger("interval[0:00:30]"), { kind: "interval", seconds: 30 });
  assert.deepEqual(parseTrigger("interval[0:01:00]"), { kind: "interval", seconds: 60 });
  assert.deepEqual(parseTrigger("interval[1:30:00]"), { kind: "interval", seconds: 5400 });
  assert.deepEqual(parseTrigger("interval[2 days, 0:00:00]"), { kind: "interval", seconds: 172_800 });
  assert.deepEqual(parseTrigger("interval[1 day, 2:03:00]"), { kind: "interval", seconds: 93_780 });
  assert.deepEqual(parseTrigger("cron[hour='8,17', minute='0']"), {
    kind: "cron",
    fields: { hour: "8,17", minute: "0" },
  });
  assert.deepEqual(parseTrigger("date[2026-11-23 14:00:00 EST]"), { kind: "date" });
  assert.deepEqual(parseTrigger("or[cron[hour='1'], cron[hour='2']]"), { kind: "other" });
});

test("intervals read in words", () => {
  assert.equal(intervalWords(15), "Every 15 seconds");
  assert.equal(intervalWords(30), "Every 30 seconds");
  assert.equal(intervalWords(60), "Every minute");
  assert.equal(intervalWords(300), "Every 5 minutes");
  assert.equal(intervalWords(5400), "Every 90 minutes");
  assert.equal(intervalWords(3600), "Every hour");
  assert.equal(intervalWords(86_400), "Every day");
  assert.equal(intervalWords(1), "Every second");
  assert.equal(intervalWords(0), null);
});

test("the production schedules read in words, in the trigger's time zone", () => {
  const words = (trigger: string) => scheduleWords(job(trigger), new Date(NOW));
  assert.equal(words("cron[hour='8,17', minute='0']"), "Every day at 8:00 am and 5:00 pm Eastern");
  assert.equal(words("interval[0:00:15]"), "Every 15 seconds");
  assert.equal(
    words("cron[day_of_week='0-6', hour='9,17', minute='0']"),
    "Every day at 9:00 am and 5:00 pm Eastern",
  );
  assert.equal(
    words("cron[day_of_week='0,1,2,3,4', hour='8,11,14,17', minute='0']"),
    "Monday to Friday at 8:00 am, 11:00 am, 2:00 pm and 5:00 pm Eastern",
  );
  assert.equal(words("cron[hour='3', minute='30']"), "Every day at 3:30 am Eastern");
  assert.equal(words("interval[0:01:00]"), "Every minute");
  assert.equal(words("interval[0:00:30]"), "Every 30 seconds");
});

test("other cron shapes read in words or fall back to the raw text", () => {
  assert.equal(cronWords({ day_of_week: "mon-fri", hour: "9" }, ET), "Monday to Friday at 9:00 am Eastern");
  assert.equal(cronWords({ day_of_week: "sat,sun", hour: "10", minute: "5" }, ET), "Saturday and Sunday at 10:05 am Eastern");
  assert.equal(cronWords({ day_of_week: "0,2,4", hour: "12" }, ET), "Monday, Wednesday and Friday at 12:00 pm Eastern");
  assert.equal(cronWords({ day_of_week: "6", hour: "0" }, ET), "Every Sunday at 12:00 am Eastern");
  assert.equal(cronWords({ hour: "8-10", minute: "0" }, ET), "Every day at 8:00 am, 9:00 am and 10:00 am Eastern");
  assert.equal(cronWords({ minute: "0" }, ET), "Every hour, on the hour");
  assert.equal(cronWords({ minute: "15" }, "UTC"), "Every hour at :15");
  // A field left out is "*" above a field given, its lowest value below one (APScheduler's rule).
  assert.equal(cronWords({ day_of_week: "0-4", minute: "15" }, ET), "Monday to Friday (Eastern), every hour at :15");
  assert.equal(cronWords({ day_of_week: "0-4" }, ET), "Monday to Friday at 12:00 am Eastern");
  assert.equal(cronWords({ hour: "3", minute: "30" }, "UTC"), "Every day at 3:30 am UTC");
  assert.equal(cronWords({ hour: "3", minute: "30" }, "Europe/Rome"), "Every day at 3:30 am Europe/Rome");
  assert.equal(cronWords({ hour: "3", minute: "30" }, null), "Every day at 3:30 am");
  // Steps, months and seconds are left to the raw text.
  assert.equal(cronWords({ hour: "*/2", minute: "0" }, ET), null);
  assert.equal(cronWords({ month: "6-8", hour: "1" }, ET), null);
  assert.equal(cronWords({ second: "30" }, ET), null);
  assert.equal(cronWords({ hour: "0-23", minute: "0" }, ET), null, "too many times to list");
  assert.equal(scheduleWords(job("or[cron[hour='1'], cron[hour='2']]"), new Date(NOW)), null);
});

test("a one-off job reads when it runs", () => {
  assert.equal(
    scheduleWords(job("date[2026-11-24 09:00:00 EST]", ET, "2026-11-24T09:00:00-05:00"), new Date(NOW)),
    "Once, Tue, Nov 24, 9:00 am ET",
  );
  assert.equal(scheduleWords(job("date[2026-11-24 09:00:00 EST]"), new Date(NOW)), "Once");
});

test("time zones read as the team says them", () => {
  assert.equal(zoneName(ET), "Eastern");
  assert.equal(zoneName("US/Eastern"), "Eastern");
  assert.equal(zoneName("Etc/UTC"), "UTC");
  assert.equal(zoneName("Asia/Tokyo"), "Asia/Tokyo");
  assert.equal(zoneName(null), "");
});

test("durations and relative times", () => {
  assert.equal(durationText(23_400), "23 s");
  assert.equal(durationText(59_999), "59 s");
  assert.equal(durationText(4 * 60_000 + 50_000), "4 min");
  assert.equal(durationText(125 * 60_000), "2 h 5 min");
  assert.equal(durationText(3 * 3_600_000), "3 h");
  assert.equal(durationText(27 * 3_600_000), "1 day 3 h");
  assert.equal(durationText(48 * 3_600_000), "2 days");
  assert.equal(relativeText(NOW + 23_000, NOW), "in 23 s");
  assert.equal(relativeText(NOW - 40 * 60_000, NOW), "40 min ago");
  assert.equal(relativeText(NOW + 400, NOW), "now");
  assert.equal(relativeText(NOW - 400, NOW), "just now");
});

test("clock times are Eastern, whatever the reader's zone, with the day when it is not today", () => {
  const now = new Date(NOW);
  assert.equal(clockText(new Date("2026-11-23T19:21:08Z"), now), "2:21:08 pm ET");
  assert.equal(clockText(new Date("2026-11-23T22:00:00Z"), now), "5:00 pm ET");
  assert.equal(clockText(new Date("2026-11-24T13:00:00Z"), now), "Tue, Nov 24, 8:00 am ET");
  // 1:30 am UTC on the 24th is still the 23rd in Eastern time.
  assert.equal(clockText(new Date("2026-11-24T01:30:00Z"), now), "8:30 pm ET");
  assert.equal(clockText(new Date("2027-01-04T17:00:00Z"), now), "Mon, Jan 4, 2027, 12:00 pm ET");
  // Summer time: 8:00 am EDT is 12:00 UTC.
  assert.equal(clockText(new Date("2026-07-01T12:00:00Z"), now), "Wed, Jul 1, 8:00 am ET");
});

test("a next run ahead is upcoming", () => {
  const next = new Date(NOW + 23_000).toISOString();
  assert.deepEqual(nextRun(job("cron[hour='14']", ET, next), NOW - 5_000, NOW), {
    kind: "upcoming",
    at: NOW + 23_000,
    projected: false,
  });
});

test("a past next run is never shown as next", () => {
  // Loaded 40 minutes ago, when 1:47 pm was still ahead: the list is out of date.
  const loadedAt = NOW - 40 * 60_000;
  const cron = job("cron[hour='13', minute='47']", ET, new Date(NOW - 34 * 60_000).toISOString());
  assert.deepEqual(nextRun(cron, loadedAt, NOW), { kind: "passed", at: NOW - 34 * 60_000 });
  // An interval job is not worked forward from a stale list either.
  const every30 = job("interval[0:00:30]", ET, new Date(NOW - 34 * 60_000).toISOString());
  assert.equal(nextRun(every30, loadedAt, NOW).kind, "passed");
});

test("a fresh interval job's next run is worked out on its beat once the listed one passes", () => {
  const listed = NOW - 4_000;
  const loadedAt = NOW - 10_000;
  assert.deepEqual(nextRun(job("interval[0:00:15]", ET, new Date(listed).toISOString()), loadedAt, NOW), {
    kind: "upcoming",
    at: listed + 15_000,
    projected: true,
  });
  // Exactly on a beat counts as passed: the next one is a full interval on.
  assert.deepEqual(nextRun(job("interval[0:00:15]", ET, new Date(NOW).toISOString()), loadedAt, NOW), {
    kind: "upcoming",
    at: NOW + 15_000,
    projected: true,
  });
  // A cron job that has just passed waits for the list instead.
  assert.deepEqual(nextRun(job("cron[minute='20']", ET, new Date(listed).toISOString()), loadedAt, NOW), {
    kind: "passed",
    at: listed,
  });
});

test("a next run already past when the list loaded means the scheduler is behind", () => {
  const loadedAt = NOW - 2_000;
  const late = loadedAt - OVERDUE_AFTER_MS - 1_000;
  assert.deepEqual(nextRun(job("interval[0:00:15]", ET, new Date(late).toISOString()), loadedAt, NOW), {
    kind: "overdue",
    at: late,
  });
  const slightlyLate = loadedAt - 5_000;
  assert.equal(nextRun(job("cron[minute='20']", ET, new Date(slightlyLate).toISOString()), loadedAt, NOW).kind, "passed");
});

test("a job with no next run says why", () => {
  assert.deepEqual(nextRun({ ...job("interval[0:00:30]"), pending: true }, NOW, NOW), {
    kind: "none",
    text: "Not started: the scheduler is not running here",
  });
  assert.deepEqual(nextRun(job("interval[0:00:30]"), NOW, NOW), { kind: "none", text: "Paused: no next run" });
});

test("the list goes stale and refreshes on its own", () => {
  assert.equal(isStale(NOW - STALE_AFTER_MS, NOW), false);
  assert.equal(isStale(NOW - STALE_AFTER_MS - 1, NOW), true);
  assert.equal(refreshDue(NOW - REFRESH_EVERY_MS + 1, NOW), false);
  assert.equal(refreshDue(NOW - REFRESH_EVERY_MS, NOW), true);
  assert.ok(REFRESH_EVERY_MS >= 15_000 && REFRESH_EVERY_MS <= 30_000);
});

test("a failure says why", () => {
  assert.equal(failureText({ detail: "Job with id x not found" }, 404), "Job with id x not found");
  assert.equal(failureText({ detail: [{ msg: "field required" }, { msg: "bad id" }] }, 422), "field required; bad id");
  assert.equal(failureText(null, 502), "The server answered 502.");
  assert.equal(failureText({ detail: "" }, 500), "The server answered 500.");
});
