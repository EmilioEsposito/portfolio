# logfire-investigate

Self-contained GitHub Actions routine: once per day, pull recent Logfire
exceptions and fire a Claude Code routine **exactly once** so it investigates and
opens a PR. Pull model only — no webhooks, no hosted endpoint.

Everything for this job lives in this folder, except the workflow YAML itself
(GitHub only discovers workflows under `.github/workflows/`).

```
.github/
├── workflows/logfire_investigate.yml   # cron trigger (must live here)
└── logfire-investigate/
    ├── investigate.py                  # all the logic (stateless)
    ├── ignore_signatures.txt           # git-tracked "Ignored" suppression list
    ├── history.log                     # append-only audit log of fires
    └── README.md
```

## How it works

1. Compute a **fixed lookback window**: `min_timestamp = now − LOOKBACK_HOURS`
   (default **28h** = the 24h cron period + a 4h buffer). No stored state.
2. Run every query in `SOURCES` (in `investigate.py`) against Logfire
   `/v1/query` with that `min_timestamp`, and union the rows (deduped on
   `(trace_id, span_id)`).
3. Group rows by Logfire exception **fingerprint** (with a
   `type/service/span_name` fallback for non-fingerprinted error-level records).
4. Drop groups matching any line in `ignore_signatures.txt`.
5. If anything remains, fire the routine **once** with a single batched payload
   (an extra turn appended to the routine's server-side session).
6. On HTTP 200: append a line to `history.log` and the workflow commits it back.
   On non-200 (incl. 429): nothing is committed and the job fails (visible in the
   Actions tab); the same window is naturally re-covered on the next run.

## Why no watermark / stored state?

An earlier design tracked a watermark (`last_run.txt`) committed back to main to
get "exactly-once" reads. It was dropped because it bought very little:

- **It does not suppress recurring errors.** A chronic error emits *new rows with
  new timestamps* every day, which clear any watermark, so the routine fires
  daily regardless. The thing that actually suppresses known noise is
  `ignore_signatures.txt` — not a watermark.
- The watermark's only real value was avoiding re-reading the *same* rows and
  gap-free coverage after multi-day outages — minor for a once-daily job — while
  it introduced a fragile 24h boundary (it once *missed* a real alert sitting
  right on the edge) plus a whole commit-back-to-main machinery.

A fixed lookback with a buffer is simpler, never misses a boundary event, and
needs no state. Worst case, an event in the overlap buffer fires on two
consecutive days (at most one duplicate). The Actions run log is itself the audit
of every run; `history.log` additionally records the fires in-repo.

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
The lookback is overridable via a `LOOKBACK_HOURS` env var (defaults to 28).

```bash
gh secret set LOGFIRE_GHA_TOKEN  --repo emilioesposito/portfolio --body '<logfire read token>'
gh secret set CC_ROUTINE_TOKEN   --repo emilioesposito/portfolio --body 'sk-ant-oat01-...'
gh secret set CC_ROUTINE_ID      --repo emilioesposito/portfolio --body 'trig_01AxnXWmCDiKhxgSWFHxiBNQ'
# optional:
gh variable set LOGFIRE_BASE     --repo emilioesposito/portfolio --body 'https://logfire-eu.pydantic.dev'
```

## Manual run / dry run

Trigger from the Actions tab (`workflow_dispatch`) with **dry_run = true** to
print the batched payload without firing. The script also accepts `--dry-run`.

## Note on the history.log commit

When a routine fires, the workflow appends to `history.log` and commits it to the
branch it ran on (the default branch for scheduled runs) as `github-actions[bot]`,
with `[skip ci]`. If the default branch later gains protection requiring PR
review, this direct push would be rejected (the fire still happened; only the
in-repo audit line would be lost) — loosen protection for the bot or switch the
commit step to open a PR.
