# logfire-investigate

Self-contained GitHub Actions routine: once per day, pull **new** Logfire
exceptions and fire a Claude Code routine **exactly once** so it investigates and
opens a PR. Pull model only — no webhooks, no hosted endpoint.

Everything for this job lives in this folder, except the workflow YAML itself
(GitHub only discovers workflows under `.github/workflows/`).

```
.github/
├── workflows/logfire_investigate.yml   # cron trigger (must live here)
└── logfire-investigate/
    ├── investigate.py                  # all the logic
    ├── last_run.txt                    # watermark (ISO8601 UTC, single line)
    ├── ignore_signatures.txt           # git-tracked "Ignored" suppression list
    ├── history.log                     # append-only audit log
    └── README.md
```

## How it works

1. Read the watermark from `last_run.txt` (defaults to now − 24h if empty).
2. Run every query in `SOURCES` (in `investigate.py`) against Logfire
   `/v1/query` with `min_timestamp = watermark`, and union the rows (deduped on
   `(trace_id, span_id)`).
3. Group rows by Logfire exception **fingerprint** (with a
   `type/service/span_name` fallback for non-fingerprinted error-level records).
4. Drop groups matching any line in `ignore_signatures.txt`.
5. If anything remains, fire the routine **once** with a single batched payload
   (an extra turn appended to the routine's server-side session).
6. On HTTP 200: advance the watermark to the newest `start_timestamp` seen,
   append a line to `history.log`, and the workflow commits both files back.
   On non-200 (incl. 429): nothing is committed, the job fails, and the same
   window is retried tomorrow.

## Why `SOURCES` is in the script, not fetched live

There are two kinds of alert in this project:

- **Explicit SQL alerts** I defined (persistent `alert_id`).
- The **auto "Issues" stream** — new fingerprinted exceptions that trigger their
  own alerts via a hidden filter alert with no user-facing id.

Live-fetching the explicit alerts' SQL needs the management API
(`api-us.pydantic.dev`, alerts CRUD), which is **OAuth2-authorization-code only**
(scope `project:read_alert`). The Logfire **read token** used here
(`LOGFIRE_GHA_TOKEN`, for `/v1/query` on `logfire-us…`) cannot call it. So both
alert kinds are mirrored as editable `SOURCES` WHERE-clauses run through
`/v1/query`. **To resync after editing an alert in the Logfire UI, copy its WHERE
clause into the matching `SOURCES` entry.**

## Suppressing a signature ("Ignored")

The read token can't read Logfire's native "Ignored" issue state, so
`ignore_signatures.txt` is the git-tracked stand-in. Add one substring per line;
it's matched case-insensitively against `type | service | label | fingerprint`.

## Configuration

Repo **secrets**:

| Secret | Value |
|--------|-------|
| `LOGFIRE_GHA_TOKEN` | Logfire read token (Bearer) |
| `CC_ROUTINE_TOKEN`  | Claude Code per-routine OAuth token (`sk-ant-oat01-…`) |
| `CC_ROUTINE_ID`     | Routine id (`trig_…`) |

Optional repo **variable** `LOGFIRE_BASE` (defaults to
`https://logfire-us.pydantic.dev`; set to `https://logfire-eu.pydantic.dev` for EU).

```bash
gh secret set LOGFIRE_GHA_TOKEN  --repo emilioesposito/portfolio --body '<logfire read token>'
gh secret set CC_ROUTINE_TOKEN   --repo emilioesposito/portfolio --body 'sk-ant-oat01-...'
gh secret set CC_ROUTINE_ID      --repo emilioesposito/portfolio --body 'trig_01AxnXWmCDiKhxgSWFHxiBNQ'
# optional:
gh variable set LOGFIRE_BASE     --repo emilioesposito/portfolio --body 'https://logfire-eu.pydantic.dev'
```

## Manual run / dry run

Trigger from the Actions tab (`workflow_dispatch`) with **dry_run = true** to
print the batched payload without firing or advancing the watermark. The script
also accepts `--dry-run` directly.

## Note on the watermark commit

When a routine fires, the workflow commits `last_run.txt` + `history.log` back to
the branch it ran on (the default branch for scheduled runs) as
`github-actions[bot]`, with `[skip ci]`. If the default branch has protection
requiring PR review, this direct push will be rejected and the watermark won't
advance — loosen protection for the bot, or switch the commit step to open a PR.
