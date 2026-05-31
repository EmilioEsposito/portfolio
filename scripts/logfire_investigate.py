#!/usr/bin/env python3
"""Pull new Logfire exceptions and fire a Claude Code routine once per run.

Pull model only (no webhooks). Designed to run once per day from a GitHub
Actions cron. On any given run it:

  1. Reads the stored watermark (``.logfire/last_run.txt``); defaults to now-24h.
  2. Queries Logfire for exception rows newer than the watermark.
  3. Groups rows by (exception_type, exception_message, service_name).
  4. Drops groups whose "exception_type | service_name" matches an entry in
     ``.logfire/ignore_signatures.txt`` (case-insensitive substring).
  5. If anything remains, fires the Claude Code routine EXACTLY ONCE with a
     single batched payload, and only then advances the watermark.

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

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGFIRE_DIR = os.path.join(REPO_ROOT, ".logfire")
WATERMARK_FILE = os.path.join(LOGFIRE_DIR, "last_run.txt")
IGNORE_FILE = os.path.join(LOGFIRE_DIR, "ignore_signatures.txt")
HISTORY_FILE = os.path.join(LOGFIRE_DIR, "history.log")

MAX_PAYLOAD_CHARS = 60000  # well under the 65536 API ceiling
MAX_SAMPLE_TRACES = 5
LOOKBACK_HOURS = 24
HTTP_TIMEOUT = 60

# The WHERE condition below mirrors my existing Logfire SQL alert, MINUS the
# time-interval clause (that part is handled here via min_timestamp + watermark).
#   Original alert:
#     ... AND service_name in ('fastapi','react-router','expo')
#         AND exception_type != 'HTTPException'
#         AND start_timestamp > now() - interval '10 minutes'
ALERT_CONDITION = (
    "service_name IN ('fastapi', 'react-router', 'expo') "
    "AND exception_type != 'HTTPException'"
)

SQL = (
    "SELECT start_timestamp, trace_id, exception_type, exception_message, service_name\n"
    "FROM records\n"
    "WHERE is_exception = true\n"
    f"  AND {ALERT_CONDITION}\n"
    "ORDER BY start_timestamp"
)


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
    signature = f"{group['exception_type']} | {group['service_name']}".lower()
    return any(p in signature for p in patterns)


def query_logfire(base: str, token: str, min_timestamp: str) -> list[dict]:
    url = f"{base.rstrip('/')}/v1/query"
    headers = {"Authorization": f"Bearer {token}"}
    params = {
        "sql": SQL,
        "min_timestamp": min_timestamp,
        "row_oriented": "true",
    }
    resp = requests.get(url, headers=headers, params=params, timeout=HTTP_TIMEOUT)
    if resp.status_code != 200:
        log(f"Logfire query failed: HTTP {resp.status_code}")
        log(resp.text[:2000])
        resp.raise_for_status()
    return extract_rows(resp.json())


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


def group_rows(rows: list[dict]) -> list[dict]:
    """Group rows by (exception_type, exception_message, service_name)."""
    groups: dict[tuple, dict] = {}
    for r in rows:
        et = (r.get("exception_type") or "").strip()
        em = (r.get("exception_message") or "").strip()
        sn = (r.get("service_name") or "").strip()
        ts = (r.get("start_timestamp") or "").strip()
        tid = (r.get("trace_id") or "").strip()
        key = (et, em, sn)
        g = groups.get(key)
        if g is None:
            g = {
                "exception_type": et,
                "exception_message": em,
                "service_name": sn,
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
    lines = ["Investigate and address these Logfire alerts:", ""]
    for i, g in enumerate(groups, 1):
        traces = ", ".join(g["traces"][:MAX_SAMPLE_TRACES]) or "(none captured)"
        lines.append(f"[{i}] {g['exception_type']} in {g['service_name']}")
        lines.append(f"    message: {g['exception_message']}")
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
        rows = query_logfire(base, read_token, watermark)
    except requests.RequestException as exc:
        log(f"ERROR: Logfire query failed: {exc}")
        set_output("fired", "0")
        return 1

    log(f"Rows returned: {len(rows)}")

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
