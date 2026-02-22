# Slack Approval for Claude Code

Forward Claude Code permission requests to Slack so you can approve/deny from your phone.

## How it works

```
YOUR LAPTOP                           EXTERNAL
─────────────────────────────────     ──────────────────────────

Claude Code                           Slack
  │                                     ▲       │
  │ permission needed                   │       │
  ▼                                     │       │
slack_approve.py (hook)                 │       │
  │                                     │       │
  ├──── POST message ───────────────────┘       │
  │     (via SLACK_WEBHOOK_CLAUDE_CODE)         │
  │                                             │
  ├──── POST /register ──┐                      │
  │                      ▼                      │
  │          Railway Approval Server ◄── POST /slack/action
  │          (stores decisions)          (user tapped button)
  │                      │
  ├──── GET /decision ───┘  (polls every 1s, up to 10min)
  │
  ▼
Claude Code proceeds (allow/deny)
```

Your laptop only makes **outbound** requests. The approval server on Railway
is the only thing that receives inbound connections (from Slack button taps).

## Setup

### 1. Slack App (one-time)

You already have `SLACK_WEBHOOK_CLAUDE_CODE` for sending messages.
For receiving button clicks, you also need:

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → your app
2. **Interactivity & Shortcuts** → toggle ON
3. Set **Request URL** to:
   ```
   https://claude-approval-production.up.railway.app/slack/action
   ```
4. Save

### 2. Environment variable

`SLACK_WEBHOOK_CLAUDE_CODE` must be set in your shell environment.
That's it — the Railway URL is hardcoded in the hook script.

### 3. Use Claude Code normally

Nothing to start. The approval server is always running on Railway.
When Claude Code tries something not in the allow list, you'll get a
Slack message with Approve / Deny buttons.

## What gets sent to Slack

Only operations NOT in the allow list in `.claude/settings.json`. Currently:
- `git push`, `git commit`, `git add`, `git checkout`, `git merge`, `git rebase`
- Any bash command not explicitly allowed (e.g. `rm`, `sed`, unknown scripts)
- Anything else Claude tries that isn't pre-approved

File edits (`Edit`, `Write`, `Read`), running your app (`pnpm`, `node`, `python`),
and read-only git are all auto-approved and never hit Slack.

## Fallback

If the approval server is down or `SLACK_WEBHOOK_CLAUDE_CODE` isn't set,
the hook exits with code 2 and Claude Code falls back to the normal local
terminal prompt. Nothing breaks.

## Files

| File | Purpose |
|------|---------|
| `slack_approve.py` | Hook script — sends Slack msg, polls for decision (stdlib only) |
| `approval_server.py` | FastAPI server — receives Slack clicks, serves decisions |
| `requirements.txt` | Python deps for Railway deployment (fastapi, uvicorn) |

## Redeploying

```bash
railway up --service claude-approval -d .claude/hooks
```

## Health check

```bash
curl https://claude-approval-production.up.railway.app/health
```
