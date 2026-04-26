# Zillow Email: Legacy → Sernia AI Migration

> **Status**: Phase 1-3 complete. Sernia AI scheduled triggers enabled. Legacy jobs disabled.
> **Last Updated**: 2026-03-07

## Goal

Migrate scheduled Zillow email processing from the legacy `zillow_email/service.py` (DB-based, inline AI, direct API calls) to Sernia AI (Gmail API tools, skill-based instructions, approval-gated actions).

## Architecture Decisions

### Inbox Architecture (solved)
The key architectural challenge: emails arrive in emilio@serniacapital.com, but replies go from all@serniacapital.com. Solution:

| Operation | Inbox Used | Why |
|-----------|-----------|-----|
| **Triage / discovery** | emilio@ | Honors archiving — Emilio curates which leads to process by archiving ones to ignore |
| **Thread reading** | all@ (fallback: emilio@) | Replies sent from shared mailbox are only visible there |
| **Sending replies** | all@ (automatic) | `send_external_email` always uses shared mailbox |
| **Calendar** | all@ (automatic) | Shared team calendar |

This is documented in the `zillow-auto-reply` SKILL.md and in the scheduled trigger prompt.

### Gmail API vs DB
Sernia AI reads directly from Gmail API (not the DB). This is simpler and avoids sync lag. The `user_email_account` parameter on tools provides the flexibility to target different mailboxes.

## Migration Plan

### Phase 1: `read_email_thread` tool — DONE
- Added `read_email_thread` tool to `api/src/sernia_ai/tools/google_tools.py`
- Uses Gmail `threads().get()` to fetch all messages in chronological order
- HTML-to-markdown conversion, per-message and total output caps
- Documented in `api/src/sernia_ai/instructions.py`

### Phase 2: Inbox architecture in triggers & SKILL.md — DONE
- Updated `email_scheduled_trigger.py` Zillow check to search emilio@ for triage, read threads from all@
- Updated `zillow-auto-reply/SKILL.md` "Shared Inbox" → "Inbox Architecture" section with clear rules

### Phase 3: Enable Sernia AI triggers, disable legacy — DONE
- Enabled `register_scheduled_triggers()` in `api/index.py`
- Disabled `register_zillow_apscheduler_jobs()` in `api/index.py`
- Legacy code preserved (not deleted) for reference/rollback

### Phase 4: Monitor & Validate — IN PROGRESS
- [ ] Deploy to dev, watch Logfire for scheduled trigger runs
- [ ] Verify Zillow email check runs at expected times (8am, 11am, 2pm, 5pm ET)
- [ ] Verify general email check runs every 3 hours
- [ ] Confirm thread reading works correctly (all@ with emilio@ fallback)
- [ ] Confirm agent correctly identifies unreplied leads and drafts responses
- [ ] Test approval flow end-to-end (agent drafts reply → SMS to Emilio → approval → email sent)

### Phase 5: Cleanup (after validation)
- [ ] Remove legacy `zillow_email/service.py` scheduled job functions
- [ ] Remove or archive `zillow_email/*.sql` files
- [ ] Remove `register_zillow_apscheduler_jobs` import from `api/index.py`
- [ ] Consider: knowledge-driven scheduled jobs (load instructions from workspace files instead of hardcoded trigger prompts)

## Key Files

| File | Role |
|------|------|
| `api/src/sernia_ai/triggers/scheduled_triggers.py` | Scheduled triggers + APScheduler registration |
| `api/src/sernia_ai/triggers/zillow_email_event_trigger.py` | Real-time Pub/Sub trigger (already Sernia AI) |
| `api/src/sernia_ai/workspace/.claude/skills/zillow-auto-reply/SKILL.md` | Lead qualification, tone, calendar rules |
| `api/src/sernia_ai/tools/google_tools.py` | Email/calendar/drive tools |
| `api/src/zillow_email/service.py` | **Legacy** — preserved for rollback |
| `api/index.py` | Scheduler registration (legacy disabled, Sernia AI enabled) |
