# Logfire Dashboard Management

Guide for creating and managing Pydantic Logfire dashboards via MCP tools.

## Tools

All dashboard operations use `mcp__logfire__dashboard_*` tools. Key ones:

- `dashboard_list` / `dashboard_get` / `dashboard_create` / `dashboard_update` / `dashboard_delete`
- `dashboard_add_panel` / `dashboard_update_panel` / `dashboard_remove_panel`
- `dashboard_add_variable` / `dashboard_update_variable` / `dashboard_update_variables`
- `dashboard_create_group` / `dashboard_delete_group` / `dashboard_rename_group`
- `dashboard_export` / `dashboard_import`

Always pass `project: "portfolio"` since the token is not project-scoped.

## Variable Substitution

**Critical**: Logfire substitutes `$variable_name` with the **quoted** value (including single quotes).

```sql
-- WRONG: produces ''Cost'' (double-quoted, invalid SQL)
CASE WHEN '$metric' = 'Runs' THEN ...

-- CORRECT: $metric becomes 'Cost', producing valid 'Cost' = 'Runs'
CASE WHEN $metric = 'Runs' THEN ...
```

This applies to all variable types. Never wrap `$variable` in quotes — Logfire adds them automatically.

### Built-in Variables

- `$resolution` — Time bucket interval (e.g., `3 hours`, `1 day`). Used in `time_bucket($resolution, start_timestamp)`. Automatically available on dashboards with time series panels; does not need to be defined in the variables list.

### Custom Variables

Define via `dashboard_add_variable` with `kind: "ListVariable"`:

```json
{
  "name": "metric",
  "display_name": "Metric",
  "kind": "ListVariable",
  "default_value": "Cost",
  "allow_multiple": false,
  "allow_all": false,
  "values": ["Runs", "Cost", "Cost per Run"]
}
```

**Variable value restrictions**: Avoid `$`, `(`, `)` in values — Logfire's substitution engine interprets these as additional variable references or breaks parsing.

## SQL Engine

Logfire uses **Apache DataFusion** (SQL syntax similar to Postgres).

- Use `->` and `->>` for JSON field access on `attributes`
- `::float` and `::bigint` casts work
- `CAST(x AS FLOAT)` also works
- CTEs (`WITH ... AS`) are fully supported
- Scientific notation (`1e6`) works
- `time_bucket($resolution, start_timestamp)` for time series bucketing
- `ROUND()`, `COALESCE()`, `SUM()`, `AVG()`, `COUNT()` all standard

## Query Types

### NonTimeSeriesQuery
For scalar values, bar charts, tables. No time bucketing needed.

```json
{"kind": "NonTimeSeriesQuery", "spec": {"plugin": {"kind": "LogfireNonTimeSeriesQuery", "spec": {"query": "SELECT ..."}}}}
```

### TimeSeriesQuery
For time series charts. Must return column `x` as the time bucket.

```json
{"kind": "TimeSeriesQuery", "spec": {"plugin": {"kind": "LogfireTimeSeriesQuery", "spec": {"query": "SELECT time_bucket($resolution, start_timestamp) AS x, ..."}}}}
```

## Panel Types

| Plugin Kind | Use For |
|-------------|---------|
| `Values` | Single stat / KPI cards |
| `BarChart` | Horizontal bar charts |
| `TimeSeriesChart` | Line/bar charts over time |
| `Table` | Tabular data |

### TimeSeriesChart with stacked bars:
```json
{"kind": "TimeSeriesChart", "spec": {"visual": {"stack": "all", "display": "bar"}}}
```

## Layout

Panels are placed in groups (collapsible sections). Each panel has grid coordinates:
- `x`: 0-23 (column position)
- `y`: row position within group
- `width`: 1-24 (columns wide, 24 = full width)
- `height`: typical 4 for KPIs, 6 for charts

## Dynamic Metric Switching Pattern

Use a `$metric` variable with CASE WHEN to switch between different aggregations in a single panel:

```sql
WITH data AS (...)
SELECT dimension,
  CASE WHEN $metric = 'Runs' THEN COUNT(*) * 1.0
       WHEN $metric = 'Cost' THEN ROUND(SUM(cost), 4)
       WHEN $metric = 'Cost per Run' THEN ROUND(AVG(cost), 4)
  END AS value
FROM data
GROUP BY dimension
ORDER BY value ASC
```

Note: Use `COUNT(*) * 1.0` instead of `COUNT(*)::float` to ensure consistent float output across all CASE branches.

## Common Patterns

### Filtering by span name with special characters
Use the actual Unicode character in queries sent via MCP tools. The em-dash `—` (U+2014) is common in span names:
```sql
WHERE span_name IN ('sernia chat', 'trigger processed silently — no action needed')
```

### Reverse bar chart ordering
Wrap in subquery to sort ascending (largest bar at top in Logfire BarChart):
```sql
SELECT * FROM (SELECT ... ORDER BY value DESC) ORDER BY value ASC
```

## Debugging Dashboard Errors

1. **SQL parser errors in dashboard but not API**: Usually a variable substitution issue. Remember `$var` is substituted with the quoted value.
2. **Test queries directly**: Use `mcp__logfire__query_run` with manually substituted values to verify SQL is valid.
3. **Check panel editor**: Open panel edit view in Chrome, toggle "Show rendered query" to see the post-substitution SQL.
4. **Column number in errors**: Count characters in the stored query to find the problematic position. Variable substitutions before that position may shift the column.
