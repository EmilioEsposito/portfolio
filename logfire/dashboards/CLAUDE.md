# Logfire Dashboard Backups

Local version-controlled snapshots of Logfire dashboards (Perses-compatible JSON).

## Why

Logfire dashboards are edited through the UI and don't have built-in version history outside Logfire's own `version` counter. Keeping a JSON snapshot here gives us:

- A restorable backup if a dashboard is accidentally broken.
- Reviewable diffs when someone changes a panel.
- A reference spec when recreating a dashboard in another project.

## How to refresh a snapshot

Use the Logfire MCP `dashboard_get` tool (or `logfire` CLI) to fetch the current definition, then overwrite the file here.

From Claude Code:

```
mcp__logfire__dashboard_get(dashboard="<slug>", project="portfolio")
```

Write the returned JSON to `logfire/dashboards/<slug>.json`.

## How to restore from a snapshot

```
mcp__logfire__dashboard_update(
  dashboard="<slug>",
  project="portfolio",
  definition=<contents of logfire/dashboards/<slug>.json>,
)
```

## Current snapshots

- `llm-cost.json` — LLM Cost dashboard. 24h default time range; declares `resolution` ListVariable (TimeBucketVariable, `defaultValue: "24h"`) so the UI can save new defaults; includes the `LLMCostByTokenType` panel.
