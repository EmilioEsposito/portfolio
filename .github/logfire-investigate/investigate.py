#!/usr/bin/env python3
"""Pull new Logfire exceptions and fire a Claude Code routine once per run.

Pull model only (no webhooks). Designed to run once per day from a GitHub
Actions cron. On any given run it:

  1. Reads the stored watermark (``last_run.txt`` beside this script); defaults
     to now-24h.
  2. Runs every query in SOURCES against Logfire /v1/query (rows newer than the
     watermark) and unions the results. SOURCES mirrors both the auto "Issues"
     fingerprint stream AND my explicitly-defined SQL alerts, because the read
     token cannot reach the OAuth2-only alerts management API to live-fetch them.
  3. Groups rows by Logfire exception fingerprint (with a
     type/service/span fallback for non-fingerprinted error-level records).
  4. Drops groups matching an entry in ``ignore_signatures.txt``
     (case-insensitive substring vs type/service/label/fingerprint) -- this
     stands in for Logfire's own "Ignored" issue state, which is not exposed
     to read tokens via the API.
  5. If anything remains, fires the Claude Code routine EXACTLY ONCE with a
     single batched payload (an extra turn appended to the routine's session),
     and only then advances the watermark.

The watermark is advanced (and committed back by the workflow) ONLY when a
routine was actually fired and the fire returned HTTP 200. No fire => no
watermark change => the same window is re-read next run.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import requests

# --- Constants --------------------------------------------------------------

LOGFIRE_BASE_DEFAULT = "https://logfire-us.pydantic.dev"
ANTHROPIC_FIRE_URL = (
    "https://api.anthropic.com/v1/claude_code/routines/{routine_id}/fire"
)
ANTHROPIC_VERSION = "2023-06-01"
ANTHROPIC_BETA = "experimental-cc-routine-2026-04-01"

# State files live alongside this script (self-contained GHA folder).
HERE = os.path.dirname(os.path.abspath(__file__))
WATERMARK_FILE = os.path.join(HERE, "last_run.txt")
IGNORE_FILE = os.path.join(HERE, "ignore_signatures.txt")
HISTORY_FILE = os.path.join(HERE, "history.log")

MAX_PAYLOAD_CHARS = 60000  # well under the 65536 API ceiling
MAX_SAMPLE_TRACES = 5
LOOKBACK_HOURS = 26
HTTP_TIMEOUT = 60

# --- Source queries ---------------------------------------------------------
#
# The Logfire MANAGEMENT API (alerts CRUD on api-us.pydantic.dev) is OAuth2
# authorization-code only (scope `project:read_alert`). A Logfire READ token
# (LOGFIRE_GHA_TOKEN, used by /v1/query) cannot call it, so we cannot live-fetch
# alert SQL from the runner. Instead we mirror the relevant alert/issue WHERE
# clauses here as editable source queries, all executed via /v1/query.
#
# This covers BOTH kinds of alert:
#   B) the auto "Issues" stream  -> fingerprinted exceptions (no persistent
#      alert id; backed by a hidden filter alert in Logfire).
#   A) my explicitly-defined SQL alerts -> their exact WHERE clauses.
#
# To keep these in sync with Logfire, copy the alert's WHERE clause from the
# Logfire UI (or `alert_get`) into the matching entry below. Each source's rows
# are unified by fingerprint downstream, so an exception caught by more than one
# source is reported once, not twice.

# Every source SELECTs this common column set so grouping/dedup is uniform.
# `fingerprint` is Logfire's own exception fingerprint (the same value that
# powers Issues); `message` is the fallback label for error-level log records
# that are not exceptions (these have no exception_type).
COMMON_SELECT = """\
SELECT
    start_timestamp,
    trace_id,
    span_id,
    exception_type,
    exception_message,
    message,
    span_name,
    service_name,
    deployment_environment,
    attributes->>'logfire.exception.fingerprint' AS fingerprint
FROM records
WHERE {where}
ORDER BY start_timestamp"""

# Reusable predicate: anything not from the local dev environment.
NON_LOCAL = "(deployment_environment IS NULL OR deployment_environment != 'local')"

SOURCES = [
    {
        # B) Mirrors Logfire's auto "Issues" saved search (fingerprinted
        #    exceptions). New issues fire their own alerts in your settings;
        #    this reproduces that population. `... IS NOT NULL` is used instead
        #    of the jsonb `?` operator for /v1/query (DataFusion) portability.
        "name": "issues-fingerprint-stream",
        "where": (
            "is_exception = true "
            "AND attributes->>'logfire.exception.fingerprint' IS NOT NULL "
            f"AND {NON_LOCAL}"
        ),
    },
    {
        # A) Mirrors the "Error-level records (non-local)" alert. Catches
        #    error-level logs AND exceptions that Issues can miss (e.g.
        #    subprocess failures logged via logfire.error).
        "name": "error-level-records-non-local",
        "where": (
            "level >= 17 "
            "AND service_name = 'fastapi' "
            f"AND {NON_LOCAL} "
            "AND span_name NOT LIKE '%POST api.anthropic.com%' "
            "AND span_name NOT LIKE '%Run time of job%' "
            "AND COALESCE(exception_type, '') != 'pydantic_ai.exceptions.ApprovalRequired' "
            "AND NOT (span_name = 'INSERT neondb' AND message LIKE '%apscheduler_jobs%')"
        ),
    },
    {
        # A) Mirrors the "APScheduler startup failure" alert.
        "name": "apscheduler-startup-failure",
        "where": (
            "span_name = 'APScheduler background startup failed: {e}' "
            "AND is_exception = true "
            f"AND {NON_LOCAL}"
        ),
    },
]


def build_sql(where: str) -> str:
    return COMMON_SELECT.format(where=where)


# --- Helpers ----------------------------------------------------------------


def log(msg: str) -> None:
    print(msg, flush=True)


def set_output(name: str, value: str) -> None:
    """Append a key=value pair to the GitHub Actions step output file."""
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"{name}={value}\n")


def parse_ts(value: str):
    """Best-effort parse of an ISO8601 timestamp into an aware datetime."""
    if not value:
        return None
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    # Python's fromisoformat tops out at microseconds; trim extra precision.
    s = re.sub(r"(\.\d{6})\d+", r"\1", s)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def read_watermark() -> str:
    """Return the stored watermark, or now-24h (ISO8601 UTC) if absent/empty."""
    try:
        with open(WATERMARK_FILE, encoding="utf-8") as f:
            value = f.read().strip()
    except FileNotFoundError:
        value = ""
    if value:
        return value
    default = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    return default.isoformat()


def load_ignore_patterns() -> list[str]:
    """Load suppression substrings.

    Robust against malformed input: comment lines (``#...``) and
    whitespace-only lines are skipped, every line is trimmed, and the rest are
    treated as plain case-insensitive substrings (NO regex). A broken ignore
    file must never take down the alerting path, so any read error is swallowed.
    """
    patterns: list[str] = []
    try:
        with open(IGNORE_FILE, encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                patterns.append(line.lower())
    except FileNotFoundError:
        pass
    except OSError as exc:  # pragma: no cover - defensive
        log(f"WARN: could not read ignore file ({exc}); proceeding with none")
    return patterns


def is_ignored(group: dict, patterns: list[str]) -> bool:
    # Match against several facets so an ignore line can target a type, a
    # service, a fingerprint, or a log message. Mirrors Logfire's own
    # "Ignored" issue state, which the read token cannot read via the API.
    haystack = " | ".join(
        [
            group.get("exception_type", ""),
            group.get("service_name", ""),
            group.get("label", ""),
            group.get("fingerprint", ""),
        ]
    ).lower()
    return any(p in haystack for p in patterns)


def query_logfire(base: str, token: str, sql: str, min_timestamp: str) -> list[dict]:
    url = f"{base.rstrip('/')}/v1/query"
    headers = {"Authorization": f"Bearer {token}"}
    params = {
        "sql": sql,
        "min_timestamp": min_timestamp,
        "row_oriented": "true",
    }
    resp = requests.get(url, headers=headers, params=params, timeout=HTTP_TIMEOUT)
    if resp.status_code != 200:
        log(f"Logfire query failed: HTTP {resp.status_code}")
        log(resp.text[:2000])
        resp.raise_for_status()
    return extract_rows(resp.json())


def collect_rows(base: str, token: str, min_timestamp: str) -> list[dict]:
    """Run every source query and return the de-duplicated union of rows.

    A single (trace_id, span_id) can be returned by more than one source
    (e.g. a fingerprinted exception that is also an error-level record), so we
    dedupe on that pair to avoid double-counting hits.
    """
    seen: set[tuple] = set()
    merged: list[dict] = []
    for source in SOURCES:
        sql = build_sql(source["where"])
        rows = query_logfire(base, token, sql, min_timestamp)
        new = 0
        for r in rows:
            key = (r.get("trace_id"), r.get("span_id"))
            if key in seen:
                continue
            seen.add(key)
            merged.append(r)
            new += 1
        log(f"  source {source['name']}: {len(rows)} rows ({new} new)")
    return merged


def extract_rows(data) -> list[dict]:
    """Normalize a Logfire query response into a list of row dicts."""
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        rows = data.get("rows")
        if not rows:
            return []
        if isinstance(rows[0], dict):  # row_oriented=true
            return rows
        # Column-oriented fallback.
        cols = data.get("columns") or []
        names = [c if isinstance(c, str) else c.get("name") for c in cols]
        if names and isinstance(rows[0], (list, tuple)):
            return [dict(zip(names, r)) for r in rows]
    return []


def signature_key(row: dict) -> tuple:
    """Stable grouping key for a row.

    Prefer Logfire's own exception fingerprint (the Issues identity). For
    error-level log records that have no fingerprint, fall back to
    (exception_type, service_name, span_name) so they still collapse sensibly.
    """
    fp = (row.get("fingerprint") or "").strip()
    if fp:
        return ("fp", fp)
    return (
        "fallback",
        (row.get("exception_type") or "").strip(),
        (row.get("service_name") or "").strip(),
        (row.get("span_name") or "").strip(),
    )


def row_label(row: dict) -> str:
    """Human label for a group: exception type, else the span/message."""
    et = (row.get("exception_type") or "").strip()
    if et:
        return et
    return (row.get("span_name") or row.get("message") or "error-level record").strip()


def group_rows(rows: list[dict]) -> list[dict]:
    """Group rows by Logfire fingerprint (with a type/service/span fallback)."""
    groups: dict[tuple, dict] = {}
    for r in rows:
        ts = (r.get("start_timestamp") or "").strip()
        tid = (r.get("trace_id") or "").strip()
        key = signature_key(r)
        g = groups.get(key)
        if g is None:
            g = {
                "label": row_label(r),
                "exception_type": (r.get("exception_type") or "").strip(),
                "exception_message": (r.get("exception_message") or "").strip(),
                "service_name": (r.get("service_name") or "").strip(),
                "fingerprint": (r.get("fingerprint") or "").strip(),
                "hits": 0,
                "first_seen": ts,
                "last_seen": ts,
                "traces": [],
            }
            groups[key] = g
        g["hits"] += 1
        # Rows arrive ordered by start_timestamp ascending.
        if ts:
            if not g["first_seen"]:
                g["first_seen"] = ts
            g["last_seen"] = ts
        if tid and tid not in g["traces"] and len(g["traces"]) < MAX_SAMPLE_TRACES:
            g["traces"].append(tid)
    # Preserve insertion order (first occurrence == earliest).
    return list(groups.values())


def newest_timestamp(rows: list[dict]) -> str | None:
    """Return the raw start_timestamp string of the newest returned row."""
    best_dt = None
    best_raw = None
    for r in rows:
        raw = (r.get("start_timestamp") or "").strip()
        dt = parse_ts(raw)
        if dt and (best_dt is None or dt > best_dt):
            best_dt = dt
            best_raw = raw
    return best_raw


def build_payload(groups: list[dict]) -> str:
    # The routine already holds the standing instructions server-side; this
    # text is the extra turn appended to the session, so it is pure context.
    lines = [
        "New Logfire exceptions since the last run "
        "(grouped by Logfire fingerprint; investigate each via its trace):",
        "",
    ]
    for i, g in enumerate(groups, 1):
        svc = g["service_name"] or "unknown-service"
        traces = ", ".join(g["traces"][:MAX_SAMPLE_TRACES]) or "(none captured)"
        lines.append(f"[{i}] {g['label']} in {svc}")
        if g["exception_message"]:
            lines.append(f"    message: {g['exception_message']}")
        if g["fingerprint"]:
            lines.append(f"    fingerprint: {g['fingerprint']}")
        lines.append(
            f"    hits: {g['hits']}   first_seen: {g['first_seen']}"
            f"   last_seen: {g['last_seen']}"
        )
        lines.append(f"    sample traces: {traces}")
        lines.append("")
    text = "\n".join(lines)
    if len(text) > MAX_PAYLOAD_CHARS:
        text = text[:MAX_PAYLOAD_CHARS]
    return text


def fire_routine(routine_id: str, token: str, text: str) -> requests.Response:
    url = ANTHROPIC_FIRE_URL.format(routine_id=routine_id)
    headers = {
        "Authorization": f"Bearer {token}",
        "anthropic-version": ANTHROPIC_VERSION,
        "anthropic-beta": ANTHROPIC_BETA,
        "Content-Type": "application/json",
    }
    return requests.post(url, headers=headers, json={"text": text}, timeout=HTTP_TIMEOUT)


def append_history(line: str) -> None:
    with open(HISTORY_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# --- Main -------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do everything EXCEPT the fire POST and watermark write; just "
        "print the payload that would be sent.",
    )
    args = parser.parse_args()

    env_dry = os.environ.get("DRY_RUN", "").strip().lower() in ("1", "true", "yes")
    dry_run = args.dry_run or env_dry

    base = os.environ.get("LOGFIRE_BASE") or LOGFIRE_BASE_DEFAULT
    read_token = os.environ.get("LOGFIRE_GHA_TOKEN")
    routine_token = os.environ.get("CC_ROUTINE_TOKEN")
    routine_id = os.environ.get("CC_ROUTINE_ID")

    if not read_token:
        log("ERROR: LOGFIRE_GHA_TOKEN is not set.")
        set_output("fired", "0")
        return 1

    watermark = read_watermark()
    log(f"Logfire base:   {base}")
    log(f"Watermark (min_timestamp): {watermark}")
    log(f"Dry run:        {dry_run}")

    try:
        rows = collect_rows(base, read_token, watermark)
    except requests.RequestException as exc:
        log(f"ERROR: Logfire query failed: {exc}")
        set_output("fired", "0")
        return 1

    log(f"Total unique rows across sources: {len(rows)}")

    # New watermark candidate is computed across ALL returned rows (pre-filter).
    new_watermark = newest_timestamp(rows)

    groups = group_rows(rows)
    patterns = load_ignore_patterns()
    if patterns:
        kept = [g for g in groups if not is_ignored(g, patterns)]
        dropped = len(groups) - len(kept)
        if dropped:
            log(f"Suppressed {dropped} group(s) via ignore_signatures.txt")
        groups = kept

    if not groups:
        log("NO_NEW")
        set_output("fired", "0")
        return 0

    total_hits = sum(g["hits"] for g in groups)
    payload = build_payload(groups)
    log(f"Groups to report: {len(groups)}  total hits: {total_hits}")

    if dry_run:
        log("DRY RUN - payload that WOULD be sent (not firing, not advancing watermark):")
        log("-" * 72)
        log(payload)
        log("-" * 72)
        set_output("fired", "0")
        return 0

    if not routine_token or not routine_id:
        log("ERROR: CC_ROUTINE_TOKEN and CC_ROUTINE_ID are required to fire.")
        set_output("fired", "0")
        return 1

    resp = fire_routine(routine_id, routine_token, payload)
    if resp.status_code == 200:
        body = resp.json()
        session_id = body.get("claude_code_session_id", "")
        session_url = body.get("claude_code_session_url", "")
        # Advance the watermark only now that the fire succeeded.
        if new_watermark:
            with open(WATERMARK_FILE, "w", encoding="utf-8") as f:
                f.write(new_watermark + "\n")
            log(f"Advanced watermark -> {new_watermark}")
        else:
            log("WARN: no parseable timestamp in returned rows; watermark unchanged")
        now = datetime.now(timezone.utc).isoformat()
        append_history(
            f"{now}  fired  session={session_id}  groups={len(groups)}  hits={total_hits}"
        )
        log(f"Fired routine. Session: {session_url}")
        set_output("fired", "1")
        return 0

    log(f"ERROR: routine fire failed: HTTP {resp.status_code}")
    log(resp.text[:2000])
    set_output("fired", "0")
    return 1


if __name__ == "__main__":
    sys.exit(main())
