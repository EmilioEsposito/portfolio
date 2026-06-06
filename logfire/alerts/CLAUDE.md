# Logfire Alert Backups

Local version-controlled snapshots of Logfire alerts (one JSON file per alert).

## Why

Logfire alerts are created/edited through the UI or the MCP API and have no
built-in version history. Keeping a snapshot here gives us:

- A restorable record of each alert's query, thresholds, and channels.
- Reviewable diffs when an alert's logic changes.
- A place to reason about alert design alongside the code the alert watches.

These files are **snapshots, not the source of truth** — Logfire is. They are
not auto-applied; update both sides when you change an alert (see below).

## File shape

One JSON file per alert, named after the alert (kebab-case). Fields mirror the
`alert_create` / `alert_get` MCP API:

| Field | Notes |
|-------|-------|
| `id` | Logfire alert UUID (from `alert_get`). |
| `name`, `description` | Display name + rationale. |
| `active` | Whether the alert is enabled in Logfire. |
| `query` | SQL the alert evaluates; it fires based on whether rows are returned. |
| `time_window` | ISO 8601 window the query scans (e.g. `PT15M`). |
| `frequency` | How often it runs (e.g. `PT5M`). |
| `watermark` | Delay before evaluating, to let data settle. |
| `notify_when` | `starts_having_matches` / `matches_changed` / etc. |
| `channels` | Notification channel `id` + `label`. **Never** store the webhook URL/secret — only the channel UUID. |

## Common operations

### Create a new alert
1. `mcp__logfire__alert_create(...)` with the params.
2. `mcp__logfire__alert_get(alert_id=...)` to read back the canonical definition.
3. Save it here as `<alert-name>.json` (scrub the channel webhook URL).

### Edit an existing alert
1. Edit the live alert: `mcp__logfire__alert_update(alert_id=..., ...)`.
2. Re-fetch with `alert_get` and update the JSON file to match.

### Refresh from Logfire (someone edited in the UI)
1. `mcp__logfire__alert_get(alert_id=...)`.
2. Overwrite the JSON file (drop the webhook secret from `channels[].config`).

## Channels

- `2cfbbcf9-39a6-4851-ba51-416a1157c49f` — `logfire-slack` (posts to #logfire).

## Current snapshots

- `error-level-records-non-local.json` — **catch-all.** Fires on any error-level
  record/exception (`level >= 17`) from a non-local environment, with a few
  expected-noise exclusions (Anthropic retries, APScheduler misfires/re-register
  conflicts, HITL `ApprovalRequired`). The primary "something broke" alert.
- `apscheduler-startup-failure.json` — fires when APScheduler fails to start on a
  non-local env, meaning all scheduled jobs (ClickUp reminders, Sernia scheduled
  checks) are down and a redeploy is likely needed.
- `sernia-recoverable-tool-error-loop.json` — fires when a single Sernia agent
  conversation logs >=3 recoverable tool errors (warn-level `SandboxError`
  family, e.g. `workspace_edit_file` `EditError`) within 15 min. Catches a run
  stuck retrying, which the single-occurrence warning downgrade in
  `api/src/sernia_ai/tools/_logging.py` (`ErrorLoggingToolset`) intentionally
  keeps off the error-level catch-all above.
- `chat-used.json` — **inactive.** Notifies when a chat endpoint flags a record
  with `slack_alert=true`. Currently disabled (`active: false`).
