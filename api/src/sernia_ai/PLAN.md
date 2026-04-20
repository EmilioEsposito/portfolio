# Sernia AI Agent — Architecture Plan

> **Last Updated**: 2026-03-01

**Goal**: An AI agent for Sernia Capital LLC that handles SMS, email, web chat, task management, and builds institutional memory over time.

**Users**: ~5 Sernia employees. Shared context — no privacy barriers between users. All conversations are accessible team-wide.

---

## Table of Contents

1. [Technical Decisions](#technical-decisions)
2. [Directory Structure](#directory-structure)
3. [Agent Architecture](#agent-architecture)
4. [Access & Permission Model](#access--permission-model)
5. [Conversation Model](#conversation-model)
6. [Tools](#tools)
7. [Memory System](#memory-system)
8. [Triggers & Push Notifications](#triggers--push-notifications)
9. [Implementation Status](#implementation-status)
10. [Future Ideas](#future-ideas)
11. [Reference](#reference)

---

## Technical Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| **LLM (main agent)** | Claude Sonnet 4.6 (`anthropic:claude-sonnet-4-6`) | Required for `WebSearchTool` (with `allowed_domains`) and `WebFetchTool` — Anthropic-only in PydanticAI. |
| **LLM (sub-agents)** | Claude Haiku 4.5 (`anthropic:claude-haiku-4-5-20251001`) | Cost savings for summarization/compaction. |
| **Framework** | PydanticAI (latest stable API) | `instructions` list, `FileSystemToolset`, `builtin_tools`, `history_processors`. |
| **Code location** | `api/src/sernia_ai/` | Dedicated module. Imports from existing services. |
| **Conversation storage** | `agent_conversations` table | Shared with other demo agents. Columns: `modality`, `contact_identifier`, `estimated_tokens`, `metadata_`. |
| **Quo/OpenPhone** | FastMCP OpenAPI bridge + custom guards | OpenPhone REST API spec → MCP tools. Custom `send_internal_sms` / `send_external_sms` with deterministic contact gates + from-phone enforcement. |
| **Memory storage** | `pydantic-ai-filesystem-sandbox` `FileSystemToolset` | Sandboxed `.workspace/` with `.md`, `.txt`, `.json` suffixes. Custom `search_files` tool for grep. |
| **Git sync** | `memory/git_sync.py` | `.workspace/` backed by `EmilioEsposito/sernia-knowledge` GitHub repo via PAT. Clone/pull on startup, commit+push after each agent turn. |
| **Web research** | PydanticAI `WebSearchTool` + `WebFetchTool` | Domain allowlist in `config.py`. |
| **Push notifications** | W3C Web Push + VAPID | No vendor lock-in. Per-device subscriptions, auto-cleanup of expired endpoints. |
| **Triggers** | Background agent runs + push notifications | SMS via webhook extension, email via APScheduler. Agent creates web chat conversations when human attention needed. |

---

## Directory Structure

```
api/src/sernia_ai/
├── __init__.py
├── agent.py                 # Main Sernia agent definition + search_files tool
├── deps.py                  # SerniaDeps dataclass
├── config.py                # Allowed domains, thresholds, tunables
├── instructions.py          # Static + dynamic instructions (all in one file)
├── routes.py                # FastAPI routes (chat, conversations, approvals, admin)
│
├── tools/
│   ├── quo_tools.py         # FastMCP OpenAPI bridge + custom SMS tools, search_contacts
│   ├── google_tools.py      # Gmail, Calendar, Drive, Docs, Sheets, PDFs
│   ├── clickup_tools.py     # List browsing, task search, CRUD
│   ├── db_search_tools.py   # Search conversations + SMS history
│   └── code_tools.py        # Secure Python sandbox (pydantic-monty)
│
├── sub_agents/
│   ├── compact_history.py   # History compaction (token-aware, Haiku sub-agent)
│   └── summarize_tool_results.py  # Tool result summarization (Haiku sub-agent)
│
├── triggers/
│   ├── background_agent_runner.py      # Core async runner (agent outside HTTP context)
│   ├── team_sms_event_trigger.py       # Team SMS event trigger (monitor + alert)
│   ├── ai_sms_event_trigger.py         # AI SMS event trigger (direct SMS ↔ agent)
│   ├── email_scheduled_trigger.py      # Scheduled email checks (general + Zillow)
│   └── register_scheduled_triggers.py  # Registers APScheduler trigger jobs
│
├── push/
│   ├── models.py            # WebPushSubscription SQLAlchemy model
│   ├── service.py           # Push + SMS send logic (subscriptions, VAPID, delivery, team SMS)
│   ├── routes.py            # Subscribe/unsubscribe/test endpoints
│   └── README.md            # VAPID key generation, debugging guide
│
└── memory/
    ├── __init__.py          # ensure_workspace_dirs, seed content, .gitkeep
    └── git_sync.py          # Git-backed sync (clone/pull/commit/push via PAT)
```

**Frontend**:
```
apps/web-react-router/app/routes/
├── sernia-chat.tsx          # Chat UI + System Instructions admin tab
apps/web-react-router/public/
├── sw.js                    # Service Worker (push notifications only, no caching)
├── manifest.json            # PWA manifest for iOS Add to Home Screen
```

**Workspace** (git-backed via `sernia-knowledge` repo):
```
.workspace/
├── MEMORY.md                           # Long-term memory (injected every conversation)
├── daily_notes/YYYY-MM-DD_<desc>.md    # One file per topic per day
├── areas/<topic>.md                    # Deep topic knowledge
└── skills/<name>/SKILL.md              # SOPs (deferred)
```

---

## Agent Architecture

### Main Agent

```python
sernia_agent = Agent(
    MAIN_AGENT_MODEL,
    deps_type=SerniaDeps,
    instructions=[STATIC_INSTRUCTIONS, *DYNAMIC_INSTRUCTIONS],
    output_type=[str, NoAction, DeferredToolRequests],
    builtin_tools=[WebSearchTool(...), WebFetchTool(...)],
    toolsets=[filesystem_toolset, quo_toolset, google_toolset,
             clickup_toolset, db_search_toolset, code_toolset],
    history_processors=[summarize_tool_results, compact_history],
)
```

### Dependencies

```python
@dataclass
class SerniaDeps:
    db_session: AsyncSession
    conversation_id: str
    user_identifier: str            # clerk_user_id or "system:sernia-ai"
    user_name: str
    user_email: str                 # @serniacapital.com (used for Google delegation)
    modality: Literal["sms", "email", "web_chat"]
    workspace_path: Path
```

### Dynamic Instructions

All in `instructions.py`, passed as `instructions=[STATIC_INSTRUCTIONS, *DYNAMIC_INSTRUCTIONS]`:

| Function | What it injects |
|----------|----------------|
| `inject_context` | Current datetime (ET) + user name + modality |
| `inject_memory` | `.workspace/MEMORY.md` content (capped at 5k chars) |
| `inject_filetree` | ASCII tree of `.workspace/` (capped at 3k chars) |
| `inject_modality_guidance` | SMS: short/direct, email: formal, web_chat: conversational |

### Token Management

Two `history_processors` run before each model request (order matters):

1. **`summarize_tool_results`** — Shrinks oversized `ToolReturnPart`s (>10k chars) in older messages via Haiku sub-agent. Current turn results are never touched.
2. **`compact_history`** — When input tokens exceed 170k (~85% of 200k window), summarizes older half of conversation into a single summary message via Haiku sub-agent.

Both are fail-safe (preserve originals on error).

---

## Access & Permission Model

### Authentication

- **Router-level gate**: `APIRouter(dependencies=[Depends(_sernia_gate)])` on all Sernia endpoints
- **Gate logic**: Verify Clerk JWT → check for `@serniacapital.com` email → stash user on `request.state`
- **Frontend gate**: Sidebar link gated behind `isSerniaCapitalUser` check

### Shared Team Access

All ~5 Sernia employees share full access to all conversations. This is enforced by:
- **Conversation queries pass `clerk_user_id=None`** — no per-user filtering. The router-level gate ensures only authenticated `@serniacapital.com` users reach the endpoints.
- **Agent-initiated conversations** (from triggers) use `clerk_user_id="system:sernia-ai"` for the DB record but are visible to all team members through the `clerk_user_id=None` queries.
- **Any Sernia user can approve, delete, or interact** with any Sernia conversation.

### HITL Approval Gates

Write operations require human approval before execution:

| Tool | Requires Approval |
|------|:-:|
| `send_internal_sms` (internal team) | No |
| `send_external_sms` (external contacts) | Yes |
| `send_email` | Yes |
| `create_calendar_event` | Yes |
| `create_task` / `update_task` / `delete_task` | Yes |
| `createContact_v1` / `updateContactById_v1` / `deleteContact_v1` | Yes |
| All read operations | No |

**Flow**: Agent calls a gated tool → PydanticAI returns `DeferredToolRequests` → conversation pauses → push notification sent → employee approves/denies in web chat → agent resumes.

Employees can **edit tool arguments** before approving (e.g., modify SMS text).

### Google API Delegation

All Google API calls use a service account with domain-wide delegation, impersonating `emilio@serniacapital.com`. This applies to Gmail, Calendar, Drive, Docs, Sheets. The `user_email` field in `SerniaDeps` determines whose account is used.

### OpenPhone Phone Line Selection

Two separate SMS tools with deterministic gates:
- **`send_internal_sms`**: No approval. Uses `QUO_SERNIA_AI_PHONE_ID`. ALL recipients must be "Sernia Capital LLC" contacts — blocks if any are external.
- **`send_external_sms`**: Requires approval. Uses `QUO_SHARED_EXTERNAL_PHONE_ID`. ALL recipients must be external — blocks if any are internal (protects internal phone numbers from exposure).

Both support group texts (list of phone numbers). Recipients must exist as Quo contacts before messaging (enforced by both tools).

---

## Conversation Model

### Conversation ID Scheme

| Source | Format | Example |
|--------|--------|---------|
| Web Chat (user-initiated) | UUID (frontend-generated) | `a1b2c3d4-...` |
| Trigger (agent-initiated) | UUID (backend-generated) | `f5e6d7c8-...` |
| AI SMS conversation | `ai_sms_from_{digits}` (deterministic) | `ai_sms_from_14155550100` |

Web chat and trigger conversations use random UUIDs. AI SMS conversations use deterministic IDs derived from the sender's phone number, enabling upsert behavior — follow-up messages update the same conversation.

### Trigger Metadata

Agent-initiated conversations store trigger info in the `metadata_` JSON column:

**Team SMS trigger**:
```json
{
  "trigger_source": "sms",
  "trigger_phone": "+14155550100",
  "trigger_message_preview": "The heater is broken",
  "trigger_event_id": "evt_123"
}
```

**AI SMS conversation**:
```json
{
  "trigger_source": "ai_sms",
  "trigger_phone": "+14155550100",
  "trigger_contact_name": "John Doe",
  "openphone_conversation_id": "conv_abc"
}
```

The frontend uses `trigger_source` and `trigger_contact_name` to render icons (Phone/Mail) in the conversation sidebar.

### Persistence

- **Backend is authoritative** — frontend sends only the latest message, backend loads full history from DB
- **Upsert via `session.merge()`** — handles both new and existing conversations
- **`asyncio.shield()`** — protects persistence from client disconnection during streaming
- **Messages stored as** PydanticAI `ModelMessage` objects serialized to JSON

---

## Tools

### Quo / OpenPhone (messaging, contacts, calls)

| Tool | Type | Approval |
|------|------|:--------:|
| `search_contacts` | Custom (fuzzy search, 5-min cached contact list) | No |
| `send_internal_sms` | Custom (all-internal gate, group text) | No |
| `send_external_sms` | Custom (external gate, group text) | **Yes** |
| `listMessages_v1` / `getMessageById_v1` | MCP bridge | No |
| `getContactById_v1` | MCP bridge | No |
| `createContact_v1` / `updateContactById_v1` / `deleteContact_v1` | MCP bridge | **Yes** |
| `listCalls_v1` / `getCallById_v1` / `getCallSummary_v1` / `getCallTranscript_v1` | MCP bridge | No |

### Google (Gmail, Calendar, Drive)

| Tool | Approval |
|------|:--------:|
| `send_email` | **Yes** |
| `search_emails` / `read_email` | No |
| `list_calendar_events` | No |
| `create_calendar_event` | **Yes** |
| `search_drive` / `read_google_doc` / `read_google_sheet` / `read_drive_pdf` | No |

### ClickUp (Task Management)

| Tool | Approval |
|------|:--------:|
| `list_clickup_lists` / `get_tasks` / `search_tasks` | No |
| `create_task` / `update_task` / `delete_task` | **Yes** |

### Database Search

| Tool | Description |
|------|-------------|
| `search_conversations` | Full-text search across `agent_conversations.messages` JSON |
| `search_sms_history` | Keyword search across SMS messages with ±5 context messages |
| `get_contact_sms_history` | Chronological SMS history for a contact (fuzzy name match) |

### Other

| Tool | Description |
|------|-------------|
| `run_python` | Secure Python sandbox (pydantic-monty). Math, dates, regex. No FS/network. |
| `search_files` | Case-insensitive grep across workspace files |
| File tools (`read_file`, `write_file`, `edit_file`, etc.) | Sandboxed `.workspace/` access via `FileSystemToolset` |
| `WebSearchTool` / `WebFetchTool` | Web research with domain allowlist |

---

## Memory System

### Tiers

| Tier | Path | Injected? | Purpose |
|------|------|:---------:|---------|
| Long-term | `/workspace/MEMORY.md` | Yes (every turn) | Key facts, patterns, rules |
| Filetree | Entire `.workspace/` | Yes (every turn) | Agent knows what files exist |
| Daily Notes | `/workspace/daily_notes/YYYY-MM-DD_<desc>.md` | On demand | Activity logs, events |
| Areas | `/workspace/areas/<topic>.md` | On demand | Deep topic knowledge |
| Skills | `/workspace/skills/<name>/SKILL.md` | Yes (every turn) | Playbooks, SOPs — auto-injected via `SkillsToolset` |

### Server-Side vs Knowledge-Repo Error Boundary

All workspace content (memory, areas, skills) lives in the `sernia-knowledge` git repo and is editable at runtime by the agent and humans. Server-side code (`api/src/sernia_ai/`) is developer-maintained Python.

**Key principle**: Knowledge-repo content must **never** crash the server. A malformed `SKILL.md`, corrupt `MEMORY.md`, or missing file must degrade gracefully (log + skip), not take down agent runs.

This is enforced by:
- `reload_skills()` — per-directory try/except, broken skill directories are skipped
- `refresh_skills_before_run` decorator — wraps reload in try/except, agent runs with stale skills on failure
- `inject_memory` / `inject_filetree` — capped reads with fallback to empty string
- `ErrorLoggingToolset` — workspace tool errors return error strings, never raise

### Git-Backed Sync

- **On startup**: Clone or pull `EmilioEsposito/sernia-knowledge` repo
- **After each agent turn**: Stage all → commit with summary → push
- **Merge conflicts**: Agent instructed to resolve conflict markers if found
- **Requires**: `GITHUB_EMILIO_PERSONAL_WRITE_PAT` env var + `git` binary

---

## Triggers & Push Notifications

### Architecture: Web Chat as Mission Control

Web chat is the central hub where employees interact with the AI. SMS and email are **event sources** — they trigger background agent processing, and when human attention is needed, the agent creates a web chat conversation and sends a push notification.

```
SMS webhook / Email scheduler
    ↓
Background agent processing (full tool access)
    ↓
Agent decides: ─── routine ──→ NoAction output → silent (log only, may update memory)
    │
    └── needs attention ──→ Create web chat conversation
                                ↓
                           Push notification
                                ↓
                           Employee opens notification or SMS deeplink → web chat
                                ↓
                           Reviews analysis → approves/rejects/follows up
                                ↓
                           Agent executes approved actions
```

### Two SMS Modalities

There are two distinct SMS-triggered flows, routed by which phone number receives the message:

| Modality | File | Phone | Conv ID | Agent Responds Via |
|----------|------|-------|---------|-------------------|
| **Team SMS trigger** | `sms_trigger.py` | Shared team number | UUID (new per event) | Web chat only |
| **AI SMS conversation** | `ai_sms_handler.py` | AI's direct number | `ai_sms_from_{digits}` | SMS (native reply) |

### Key Design Decisions

- **Team SMS trigger**: All SMS replies require HITL approval — no auto-responding, even for simple acks. Agent monitors, analyzes, and alerts team via web chat.
- **AI SMS conversation**: Always responds — this is a direct conversation. Internal contacts only (gated on `QUO_INTERNAL_COMPANY`). HITL tools still pause for approval; post-approval, the result is sent back as SMS.
- **Agent uses `NoAction` structured output** for silent processing (routine messages). Silent runs are logged to Logfire with the reason and trigger prompt preview for auditability.
- **Per-key rate limiting (2 min cooldown)** — event-based triggers are rate-limited to prevent the same key (e.g. phone number) from firing more than once every 2 minutes. In-memory dict (`_trigger_cooldowns`), resets on restart. Rate-limited triggers are logged and skipped before the agent runs.
- **Triggers coexist with existing systems** — Twilio escalation runs alongside team SMS trigger
- **Zillow email processing subsumed** into Sernia AI agent (was separate APScheduler jobs)

### Team SMS Trigger (`sms_trigger.py`)

- **Entry point**: `handle_team_sms_event()` called as `BackgroundTask` from OpenPhone webhook
- **Fires for**: `message.received` events to the Sernia phone number (skips emoji-leading messages)
- **Agent prompt**: Includes the message + instructions to look up contact, check SMS history, decide if team needs alerting
- **Coexists with**: `analyze_for_twilio_escalation()` — both fire independently

### AI SMS Conversation (`ai_sms_handler.py`)

- **Entry point**: `handle_ai_sms_event()` called as `BackgroundTask` from OpenPhone webhook
- **Fires for**: `message.received` events to `QUO_SERNIA_AI_PHONE_ID`
- **Contact gate**: Verifies sender is a Sernia Capital LLC contact via OpenPhone API — unknown/external numbers silently ignored
- **History**: Loads from DB if conversation exists (preserves tool context). On first message, bootstraps from OpenPhone SMS thread (`GET /v1/messages`)
- **Agent modality**: `modality="sms"` — agent produces short, direct responses without markdown
- **Result handling**: Text output → sent as SMS reply. `DeferredToolRequests` (HITL) → saves to DB, sends push + SMS notification to team
- **Post-approval**: When team approves in web chat, `approve_conversation` endpoint detects `modality="sms"` and sends the agent's result back via SMS
- **Frontend**: SMS conversations appear read-only in web chat (no compose input, HITL approval cards still function)

### Email Trigger

Two scheduled jobs via APScheduler:

| Job | Frequency | Scope |
|-----|-----------|-------|
| `check_general_emails` | Every 15 min | Unread inbox (excludes Zillow, promotions, tool notifications) |
| `check_zillow_emails` | Every 5 min (8am-8pm ET) | Zillow leads with qualification criteria |

**Zillow qualification criteria** (embedded in agent instructions):
- Credit < 600 → not qualified. Credit 670+ → qualified. 600-669 → case-by-case.
- Dogs → not allowed. Cats → ok. Others → ask.
- Follow-up rules: no reply if ball is in lead's court, appointment confirmed, or lead disqualified.

### Push Notifications

- **Protocol**: W3C Web Push with VAPID (no vendor lock-in)
- **Types**: Approval (persists until acted on via `requireInteraction`) and Alert (auto-dismisses)
- **Deep-linking**: Notification click → `/sernia-chat?id={conversation_id}`
- **Delivery**: All active subscriptions (all devices of all Sernia users)
- **Auto-cleanup**: Expired endpoints (410/404) deleted on next send attempt

### SMS Team Notifications

SMS notifications sent alongside push for belt-and-suspenders delivery. Ensures team members without push enabled still get notified, and leaves a persistent record in the shared OpenPhone thread.

- **From**: `QUO_SERNIA_AI_PHONE_ID` (Sernia AI's internal line)
- **To**: Shared team number (looked up from `QUO_SHARED_TEAM_CONTACT_ID` via OpenPhone API, cached at module level)
- **Message format**: `{title}\n{body}\n\n{deeplink_url}`
- **Deeplink**: `{FRONTEND_BASE_URL}/sernia-chat?id={conversation_id}` — environment-aware (prod/dev/local)
- **Failure isolation**: SMS errors are logged via `logfire.exception()` but never re-raised — SMS failure should never block trigger flow
- **Circular safety**: AI→shared number is outbound from AI's perspective; the webhook fires on `message.received` inbound to sernia's number, so no re-trigger loop

**Agent instructions** also guide the agent to prefer the shared team number for general team notifications via `send_internal_sms`, so the agent's own messages also go to the shared thread.

**Phase 2 (AI SMS conversations)** is now implemented — see `ai_sms_handler.py` and the [Two SMS Modalities](#two-sms-modalities) section above.

### Background Runner (`triggers/background_runner.py`)

Core function shared by all triggers:

1. **Rate-limit check**: If the `rate_limit_key` (e.g. `sms:+1415...`) fired within the last 2 minutes, skip with a Logfire log and return `None`
2. Creates its own `AsyncSession` (not from FastAPI DI — runs outside HTTP context)
3. Builds `SerniaDeps` with system identity (`system:sernia-ai`, `emilio@serniacapital.com`)
4. Runs agent with the caller's synthetic `trigger_prompt` (each trigger owns its full prompt, including any decision framework or output structure it needs)
5. If `NoAction` output → silent return with Logfire logging (reason + prompt preview). Workspace changes still committed.
6. Otherwise → persists conversation, sends push notification via `asyncio.create_task`

---

## Implementation Status

| Phase | Status | Summary |
|-------|--------|---------|
| 1. Foundation | Done | Agent, deps, config, routes, HITL output type |
| 2. Memory System | Done | FileSystemToolset, git sync, workspace seeding, dynamic instructions |
| 3. Frontend Web Chat | Done | Streaming chat UI, conversation sidebar, HITL approval cards |
| 4. Workspace Admin | Partial | Auth gate, system instructions admin tab. Workspace explorer deferred (agent manages files). |
| 5. Core Tools | Done | OpenPhone, Google (9 tools), ClickUp (6 tools), DB search, code sandbox |
| 6. Sub-Agents | Done | Tool result summarization + history compaction (Haiku sub-agents) |
| 6.5. SMS History Search | Done | `search_sms_history` + `get_contact_sms_history` with trigram indexes |
| 7. Error Handling | Done | Tool errors logged but don't break conversation. Logfire alerts. |
| 8. Push Notifications | Done | W3C Web Push, VAPID, iOS PWA, approval + alert types |
| 9. Triggers | Done | SMS trigger (webhook), email trigger (APScheduler), background runner, shared conversation access |
| 10. SMS Team Notifications | Done | SMS to shared team number alongside push, deeplinks, agent shared-number preference |
| 11. AI SMS Conversations | Done | Direct SMS ↔ agent modality, internal-only gate, post-approval SMS reply, read-only web chat view |
| 12. sernia.ai domain | Not started | Railway/Cloudflare/Clerk setup |

### Remaining Cleanup

- [ ] Deprecate old Zillow email APScheduler jobs (`register_zillow_apscheduler_jobs()` in `api/index.py`) once the new Sernia AI email triggers are validated in production
- [ ] Phase 7 was marked done but should verify error handling covers trigger background tasks

---

## Future Ideas

### Near-Term

- **SMS → web chat compose**: Allow typing in web chat to reply to AI SMS conversations (currently team tells agent what to do via the SMS thread itself)
- **Auto-SMS-reply after web chat approval for team SMS**: Only AI SMS conversations (Type 2) get post-approval SMS. Team SMS triggers (Type 1) stay web-chat-only.
- **Group SMS threads**: Only 1:1 between AI phone and a team member currently supported
- **Trigger debouncing (advanced)**: Per-key rate limiting (2 min) is implemented. Future: batch multiple events from the same contact within the window into a single agent run with combined context.
- **Conversation dedup**: Before creating a new trigger conversation, check if there's a recent one about the same contact. Append if within time window.
- **Notification badge/counter**: Show unread trigger conversation count in sidebar header

### Medium-Term

- **Gmail Pub/Sub webhook for Zillow**: Replace 5-min polling with event-driven processing via the existing Pub/Sub topic (`projects/portfolio-450200/topics/gmail-notifications`)
- **Deprecate Twilio escalation**: Once SMS triggers are proven reliable, remove the separate Twilio escalation flow and let the Sernia agent handle urgency assessment
- **sernia.ai domain**: Dedicated domain for the agent's web interface
- **Skills/SOPs**: Wire up `pydantic-ai-skills` package for structured business procedures (directory already exists in workspace)

### Longer-Term

- **Agent autonomy tiers**: Configurable per-tool autonomy (e.g., auto-send simple SMS acks without approval, but always require approval for emails)
- **Multi-user routing**: Route trigger notifications to specific team members based on topic/property/contact assignment
- **Voice integration**: Process Quo call transcripts through the agent for automatic note-taking and follow-up suggestions

---

## Reference

### Key PydanticAI Patterns

| Pattern | Use For |
|---------|---------|
| `instructions=[str, *fns]` list | Static + dynamic instructions — functions take `RunContext[SerniaDeps]` |
| `FileSystemToolset` + `Sandbox` | Sandboxed file access with mount config, suffix allowlist |
| `FunctionToolset` | Grouping related tools (quo, google, clickup, etc.) |
| `builtin_tools=[WebSearchTool(), WebFetchTool()]` | Web research with domain filtering (Anthropic-only) |
| `history_processors=[fn]` | Token-aware compaction before each model call |
| `FastMCPToolset` | Bridge OpenAPI specs into PydanticAI toolsets (OpenPhone) |
| `output_type=[str, NoAction, DeferredToolRequests]` | HITL approval flow + silent triggers |
| `RunContext[SerniaDeps]` | Access deps in tools and instructions |

### Existing Code Reused

| Code | Location | Purpose |
|------|----------|---------|
| Conversation persistence | `api/src/ai_demos/models.py` | `save_agent_conversation()`, `get_conversation_messages()`, etc. |
| HITL utilities | `api/src/ai_demos/hitl_utils.py` | `resume_with_approvals()`, `extract_pending_approvals()` |
| OpenPhone webhook | `api/src/open_phone/routes.py` | Extended with team SMS trigger + AI SMS handler (alongside Twilio escalation) |
| Gmail API | `api/src/google/gmail/service.py` | `send_email()` via delegated credentials |
| Calendar API | `api/src/google/calendar/service.py` | `create_calendar_event()` |
| APScheduler | `api/src/apscheduler_service/service.py` | `get_scheduler()` for email trigger jobs |

### Environment Variables

| Variable | Purpose |
|----------|---------|
| `ANTHROPIC_API_KEY` | Main + sub-agent LLM calls |
| `OPEN_PHONE_API_KEY` | Quo/OpenPhone API access |
| `OPEN_PHONE_WEBHOOK_SECRET` | HMAC signature verification |
| `VAPID_PRIVATE_KEY` / `VAPID_PUBLIC_KEY` | Web Push signing |
| `VAPID_CLAIM_EMAIL` | Web Push identity (`mailto:admin@serniacapital.com`) |
| `GITHUB_EMILIO_PERSONAL_WRITE_PAT` | Git sync for `.workspace/` |
| `WORKSPACE_PATH` | Override workspace location (default: repo-relative `.workspace/`) |
| Google service account JSON | Gmail, Calendar, Drive delegation |
| `CLERK_SECRET_KEY` | JWT verification for auth gate |
