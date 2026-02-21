# Sernia Capital LLC AI Agent — Architecture Plan

> **Last Updated**: 2026-02-21

**Goal**: Build an all-encompassing AI agent for Sernia Capital LLC that handles SMS, email, web chat, task management, and builds institutional memory over time.

**Users**: ~5 Sernia employees. Shared context — no privacy barriers between users. All conversations are accessible cross-user for context continuity.

---

## Table of Contents

1. [Technical Decisions](#technical-decisions)
2. [Directory Structure](#directory-structure)
3. [Agent Architecture](#agent-architecture)
4. [Conversation Model](#conversation-model)
5. [Tools & Toolsets](#tools--toolsets)
6. [Memory System](#memory-system)
7. [Workspace Admin Tool](#workspace-admin-tool)
8. [Sub-Agents](#sub-agents)
9. [Triggers & Entry Points](#triggers--entry-points)
10. [Human Interaction Modalities](#human-interaction-modalities)
11. [Database Changes](#database-changes)
12. [Implementation Phases](#implementation-phases)
13. [Open Questions](#open-questions)

---

## Technical Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| **LLM (main agent)** | Claude Sonnet 4.5 (`anthropic:claude-sonnet-4-5`) | Required for `WebSearchTool` (with `allowed_domains`) and `WebFetchTool` — both are Anthropic-only features in PydanticAI. |
| **LLM (sub-agents)** | GPT-4o-mini (`openai:gpt-4o-mini`) | Cost savings for summarization/compaction work. No builtin tool dependency. (Originally planned GPT-5.2, switched to 4o-mini for cost.) |
| **Framework** | PydanticAI (latest stable API) | Already in use. Use `instructions` (not `system_prompt`), `FunctionToolset`, `builtin_tools`, `history_processors`. |
| **Code location** | `api/src/ai_sernia/` | New module. Imports from existing services (`open_phone/`, `google/`, `clickup/`, `ai/models.py`). |
| **Conversation storage** | Existing `agent_conversations` table | Add columns for modality and contact identifier. Reuse existing persistence utilities from `api/src/ai/models.py`. |
| **Quo/OpenPhone** | Evaluate MCP first, fallback to custom tools | MCP at `https://mcp.quo.com/sse` has 5 tools (send, bulk send, check messages, call transcripts, create contacts). Beta, SSE transport. See [Quo MCP Evaluation](#quo-mcp-evaluation). |
| **Memory storage** | Custom `FunctionToolset` in `memory/toolset.py` | Hand-rolled `resolve_safe_path` with suffix allowlist (`.md`, `.txt`, `.json`) and traversal protection. Simpler than `pydantic-ai-filesystem-sandbox` and avoids the extra dependency. `.workspace/` on localhost (gitignored), Railway volume in production. |
| **Skills/SOPs** | Deferred | `pydantic-ai-skills` was planned but not yet needed. Skills directory exists in workspace structure; toolset can be added later when SOPs are created. |
| **Web research** | PydanticAI `WebSearchTool` + `WebFetchTool` | Builtin tools with `allowed_domains` for safe web access. Domain allowlist in easy-to-edit config file. |

---

## Directory Structure

```
api/src/ai_sernia/
├── __init__.py
├── agent.py                 # ✅ Main Sernia agent definition
├── deps.py                  # ✅ SerniaDeps dataclass
├── config.py                # ✅ Allowed domains, thresholds, tunables
├── instructions.py          # ✅ Dynamic @agent.instructions functions
├── routes.py                # ✅ FastAPI routes (web chat, conversations, approvals, workspace admin)
│
├── tools/                   # 🔲 Phase 4
│   ├── __init__.py
│   ├── quo_tools.py         # Quo/OpenPhone: send SMS, read messages, transcripts
│   ├── google_tools.py      # Gmail, Calendar, Drive, Docs
│   ├── clickup_tools.py     # Tasks, projects
│   └── db_search_tools.py   # Search agent_conversations + open_phone_messages tables
│
├── sub_agents/              # 🔲 Phase 5
│   ├── __init__.py
│   ├── history_compactor.py # Summarize old messages for compaction
│   └── summarization.py     # Summarize large tool results
│
├── triggers/                # 🔲 Phase 6
│   ├── __init__.py
│   ├── sms_trigger.py       # Extends OpenPhone webhook → agent
│   └── email_scheduler.py   # APScheduler periodic email check → agent
│
├── memory/
│   ├── __init__.py          # ✅ resolve_safe_path, ALLOWED_SUFFIXES, ensure_workspace_dirs
│   └── toolset.py           # ✅ FunctionToolset (read/write/append/list_directory)
│
└── workspace_admin/
    ├── __init__.py
    └── routes.py            # ✅ Admin API for managing .workspace/ files (6 endpoints)
```

**Frontend for workspace admin** (in React Router app):
```
apps/web-react-router/app/routes/
└── workspace.tsx            # ✅ Single-page file explorer (state-driven nav, no nested routes)
```

**Workspace directory** (gitignored, agent-managed):
```
.workspace/
├── MEMORY.md                           # Tacit memory: patterns, rules, principles
├── daily_notes/
│   └── 2026-02-17.md                   # Daily activity logs
├── areas/
│   └── <area_name>/                    # Agent-organized knowledge (any structure)
│       └── <file_name>.md
└── skills/
    └── <skill_name>/
        ├── SKILL.md                    # SOP instructions (YAML frontmatter + markdown)
        ├── resources/                  # Reference docs
        └── scripts/                    # Executable scripts (optional)
```

---

## Agent Architecture

### Main Agent (`agent.py`) — Implemented

```python
sernia_agent = Agent(
    MAIN_AGENT_MODEL,                # anthropic:claude-sonnet-4-5
    deps_type=SerniaDeps,
    instructions=STATIC_INSTRUCTIONS,
    output_type=[str, DeferredToolRequests],  # HITL foundation
    builtin_tools=_build_builtin_tools(),     # WebSearchTool + WebFetchTool (if Anthropic)
    toolsets=[memory_toolset],                # Custom FunctionToolset (Phase 2)
    # Future: quo_toolset, google_toolset, clickup_toolset, db_search_toolset
    instrument=True,
    name=AGENT_NAME,
)
register_instructions(sernia_agent)  # Dynamic context, memory, daily notes, modality
```

**Note**: `history_processors` not yet wired (Phase 5). `DeferredToolRequests` output type enables HITL approval flow — the agent can return tool calls that need human approval before execution.

### Configuration (`config.py`) — Implemented

Easy-to-tweak file for domains, thresholds, and tunables:

```python
WEB_SEARCH_ALLOWED_DOMAINS: list[str] = [
    "zillow.com", "redfin.com", "apartments.com",
    "rentometer.com", "clickup.com", "serniacapital.com",
]

TOKEN_COMPACTION_THRESHOLD = 170_000   # ~85% of 200k context window
SUMMARIZATION_CHAR_THRESHOLD = 10_000
MAIN_AGENT_MODEL = "anthropic:claude-sonnet-4-5"
SUB_AGENT_MODEL = "openai:gpt-4o-mini"
AGENT_NAME = "sernia"

# Workspace path: Railway volume mount (/.workspace) or repo-relative fallback
WORKSPACE_PATH = Path(
    os.environ.get("WORKSPACE_PATH", Path(__file__).resolve().parents[3] / ".workspace")
)
```

### Dependencies (`deps.py`) — Implemented

```python
@dataclass
class SerniaDeps:
    db_session: AsyncSession
    conversation_id: str
    user_identifier: str            # clerk_user_id, phone number, or email
    user_name: str                  # Display name for the agent
    modality: Literal["sms", "email", "web_chat"]
    workspace_path: Path            # Path to .workspace/ sandbox root
```

Instantiated in `routes.py` for web chat, and will be instantiated by triggers for SMS/email modalities.

### Dynamic Instructions (`instructions.py`) — Implemented

Registered via `register_instructions(agent)` pattern. Each function is decorated with `@agent.instructions` (always re-evaluated, even with message_history):

1. **`inject_context`** — Current datetime (ET) + user name + modality
2. **`inject_memory`** — Reads `.workspace/MEMORY.md` (capped at 5k chars)
3. **`inject_daily_notes`** — Today's `daily_notes/YYYY-MM-DD.md` (capped at 2k chars)
4. **`inject_modality_guidance`** — SMS: short/direct, email: formal, web_chat: conversational

---

## Conversation Model

### Conversation ID Scheme

Deterministic IDs for SMS/email so the same thread always maps to the same conversation:

| Modality | Conversation ID Format | Example |
|----------|----------------------|---------|
| SMS | `sms:{e164_phone}` | `sms:+14155550100` |
| Email | `email:{gmail_thread_id}` | `email:18d5f3a2b1c4e` |
| Web Chat | UUID (frontend-generated) | `a1b2c3d4-...` |

### Token-Aware Compaction

Compaction is **modality-agnostic**. The `history_processor` monitors cumulative token usage from `ModelResponse.usage` on each message. When total tokens reach ~85% of the context window (`TOKEN_COMPACTION_THRESHOLD` in `config.py`), it triggers the `history_compactor` sub-agent to summarize older messages.

```python
async def compact_if_needed(
    ctx: RunContext[SerniaDeps],
    messages: list[ModelMessage],
) -> list[ModelMessage]:
    total_tokens = sum(
        msg.usage.input_tokens + msg.usage.output_tokens
        for msg in messages
        if isinstance(msg, ModelResponse) and msg.usage
    )

    if total_tokens < TOKEN_COMPACTION_THRESHOLD:
        return messages

    # Split: older messages get summarized, recent ones kept verbatim
    split_point = len(messages) // 2
    older = messages[:split_point]
    recent = messages[split_point:]

    summary_result = await history_compactor.run(message_history=older)
    return summary_result.new_messages() + recent
```

---

## Tools & Toolsets

### Web Research (builtin tools)

The main agent has PydanticAI's `WebSearchTool` and `WebFetchTool` as builtin tools. Both are configured with `allowed_domains` from `config.py` so the agent can only search/fetch from approved sites.

```python
builtin_tools=[
    WebSearchTool(allowed_domains=WEB_SEARCH_ALLOWED_DOMAINS),
    WebFetchTool(allowed_domains=WEB_SEARCH_ALLOWED_DOMAINS),
]
```

**Why Anthropic is required**: `allowed_domains` on `WebSearchTool` is only supported by Anthropic and Groq models. `WebFetchTool` only works with Anthropic and Google. OpenAI models silently ignore domain filtering and don't support `WebFetchTool` at all.

### Quo / OpenPhone Tools (`quo_tools.py`)

**Strategy**: Evaluate Quo MCP first. If adequate, use `MCPServerSSE`. Otherwise, build `FunctionToolset` wrapping existing `open_phone/service.py`.

Note: OpenPhone was renamed to Quo recently, but this repo still refers to it as OpenPhone. Don't rename existing code.

#### Quo MCP Evaluation

The Quo MCP server (`https://mcp.quo.com/sse`) offers 5 tools:

| Tool | Useful? | Notes |
|------|---------|-------|
| Send Text Message | Yes | Replaces our `send_message()` |
| Send Bulk Messages | Maybe | Useful for announcements |
| Check Recent Messages | Yes | Filters by contact + date range. Not full-text search. |
| Get Call Transcripts | Yes | Plan-dependent feature |
| Create Contacts | Maybe | We already have `upsert_openphone_contact()` |

**Concerns**: Beta status, SSE transport (deprecated in MCP spec), approval gates designed for interactive use, no push events, limited search (no full-text, requires exact spelling).

**Evaluation plan**: Wire up `MCPServerSSE` in a test script, authenticate, and test each tool. If message retrieval quality is poor, fall back to custom tools.

**Regardless of MCP outcome**: We still need:
- `db_search_tools.py` to search `open_phone_messages` table (full-text search the MCP can't do)
- Existing webhook handler for incoming SMS events (MCP doesn't push)

#### Custom Quo Toolset (fallback)

```python
quo_toolset = FunctionToolset()

@quo_toolset.tool
async def send_sms(ctx: RunContext[SerniaDeps], to: str, body: str) -> str:
    """Send an SMS message via Quo/OpenPhone."""
    response = send_message(message=body, to_phone_number=to)
    return f"SMS sent to {to}" if response.ok else f"Failed: {response.text}"

@quo_toolset.tool
async def get_recent_messages(ctx: RunContext[SerniaDeps], phone_number: str, days: int = 7) -> str:
    """Get recent SMS messages with a contact."""
    # Call Quo API directly (not the DB table)
    ...
```

### Google Tools (`google_tools.py`)

Build `FunctionToolset` wrapping existing services. No MCP available.

| Tool | Wraps | Priority |
|------|-------|----------|
| `send_email` | `google/gmail/service.py:send_email()` | High |
| `search_email` | Gmail API search | High |
| `read_email_thread` | Gmail API thread.get | High |
| `list_calendar_events` | `google/calendar/service.py` | Medium |
| `create_calendar_event` | `google/calendar/service.py:create_calendar_event()` | Medium |
| `search_drive` | Drive API files.list | Medium |
| `read_document` | Docs/Drive API | Low |

Uses existing service account with domain-wide delegation via `get_delegated_credentials()`.

### ClickUp Tools (`clickup_tools.py`)

Build `FunctionToolset` wrapping existing `clickup/service.py`.

| Tool | Description | Priority |
|------|-------------|----------|
| `get_tasks` | Fetch tasks from views/lists with filtering | High |
| `create_task` | Create a new task | Medium |
| `update_task` | Update task status, assignee, etc. | Medium |
| `add_comment` | Add comment to a task | Low |

### Database Search Tools (`db_search_tools.py`)

Database-backed search across internal tables. Named `db_search_tools` to distinguish from Google Drive search, email search, etc. which live in their respective toolset files.

| Tool | Description |
|------|-------------|
| `search_conversations` | Full-text search across `agent_conversations.messages` JSON. Returns conversation snippets with metadata (who, when, modality). |
| `search_sms_history` | Full-text search across `open_phone_messages` table. For historical SMS context the Quo MCP can't provide. |

---

## Memory System

### Implemented: Custom FunctionToolset (`memory/toolset.py`)

Instead of `pydantic-ai-filesystem-sandbox`, we use a hand-rolled `FunctionToolset` with a shared `resolve_safe_path()` helper in `memory/__init__.py`. This avoids an extra dependency while providing the same safety guarantees (path traversal protection, suffix allowlist).

**Shared security** (`memory/__init__.py`):
- `resolve_safe_path(workspace, relative_path)` — blocks `..` traversal, enforces `ALLOWED_SUFFIXES` (`.md`, `.txt`, `.json`)
- Used by both the agent tools (`memory/toolset.py`) AND the workspace admin API (`workspace_admin/routes.py`)

**Agent tools** (`memory/toolset.py`):
| Tool | Description |
|------|-------------|
| `read_file(path)` | Read a file (truncated at 10k chars) |
| `write_file(path, content)` | Create or overwrite a file |
| `append_to_file(path, content)` | Append to file (or create) |
| `list_directory(path)` | List directory contents |

### Skills Configuration (Deferred)

The `pydantic-ai-skills` package is not yet installed. The `skills/` directory exists in the workspace structure and can be wired up later when SOPs are created.

### Memory Tiers

| Tier | Path | Purpose | Injected? |
|------|------|---------|-----------|
| **Tacit** | `/workspace/MEMORY.md` | Patterns, rules, principles | Yes — every conversation |
| **Daily Notes** | `/workspace/daily_notes/YYYY-MM-DD.md` | Activity logs, business events | Today's note via instructions |
| **Areas** | `/workspace/areas/<topic>/<file>.md` | Organized knowledge (agent decides structure) | No — loaded on demand via tools |
| **Skills** | `/workspace/skills/<name>/SKILL.md` | SOPs, business procedures | Summary via `SkillsToolset` instructions; full content loaded via `load_skill` tool |

---

## Workspace Admin Tool — Implemented (Phase 3)

A backend API + frontend for humans to manually browse, create, edit, and delete files in the `.workspace/` directory. Gated to `@serniacapital.com` users via `SerniaUser` dependency.

### Backend (`workspace_admin/routes.py`) — Implemented

FastAPI sub-router with `prefix="/workspace"`, included in the Sernia router. All endpoints use the `SerniaUser` dependency for auth (Clerk + `@serniacapital.com` email gate). Endpoints are at `/api/ai-sernia/workspace/*`.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/ls?path=` | GET | List directory contents (empty path = workspace root) |
| `/read?path=` | GET | Read file content |
| `/write` | POST | Create or overwrite a file (body: `{path, content}`) |
| `/mkdir` | POST | Create a directory (body: `{path}`) |
| `/delete?path=` | DELETE | Delete a file or empty directory |
| `/download?path=` | GET | Download file as attachment (`FileResponse`) |

**Omitted**: `delete-all` endpoint was dropped — too dangerous for a 6-person team.

**Security**:
- All file paths validated via `resolve_safe_path()` from `memory/__init__.py` (shared with agent tools)
- Directory paths use a separate `_safe_resolve_dir()` (no suffix check, but still traversal-protected)
- Auth via `SerniaUser` (Clerk JWT + `@serniacapital.com` email verification)

### Frontend (`workspace.tsx`) — Implemented

Single-page file explorer at `/workspace` in the React Router app. Uses state-driven navigation (no nested routes).

- **Path bar** — clickable `.workspace / segment / segment` breadcrumbs (shadcn `Button variant="link"`)
- **Directory view** — entries with `Folder`/`FileText` icons, click to navigate or open
- **File view** — content in `<Textarea>` (read-only by default). Edit/Save buttons toggle editing
- **Actions** — New File, New Folder (inline inputs), Delete (with `AlertDialog` confirmation), Download
- **Auth** — `<AuthGuard>` wrapper, sidebar link gated behind `isSerniaCapitalUser`
- **Fetch pattern** — `useAuth()` → `getToken()` → `fetch()` with `Authorization: Bearer` header

---

## Sub-Agents

### History Compactor (`sub_agents/history_compactor.py`)

**Purpose**: Summarize old messages when conversation token count reaches ~85% of context window. Compaction is modality-agnostic — same threshold for SMS, email, and web chat.

**Model**: `openai:gpt-4o-mini` (configured in `config.py` as `SUB_AGENT_MODEL`)

```python
history_compactor = Agent(
    'openai:gpt-4o-mini',
    instructions="""
Summarize this conversation history concisely. Preserve:
- Key decisions and outcomes
- Action items and commitments
- Important context (names, numbers, dates, addresses)
- The emotional tone and relationship context
Omit: pleasantries, repeated information, irrelevant tangents.
""",
    name='history_compactor',
)
```

**Invocation**: Called by `history_processor.py:compact_if_needed()` when token threshold is exceeded. Not a tool — the main agent never calls it directly.

### Summarization Agent (`sub_agents/summarization.py`)

**Purpose**: Prevents large tool results (email threads, ClickUp task lists, Drive search results) from blowing up the main agent's context window.

**Model**: `openai:gpt-4o-mini`

**Pattern**: Wraps tool calls. Returns structured output indicating whether data is verbatim or summarized:

```python
class ToolResultSummary(BaseModel):
    """Structured wrapper so the main agent knows what it's getting."""
    format: Literal["verbatim", "summarized", "truncated"]
    item_count: int                    # Total items in original data
    returned_count: int                # Items included in this response
    content: str                       # The actual data or summary

summarization_agent = Agent(
    'openai:gpt-4o-mini',
    output_type=ToolResultSummary,
    instructions="...",
    name='summarization',
)
```

**Usage in tools**: Tools that fetch potentially large data call the summarization agent before returning:

```python
@google_toolset.tool
async def search_email(ctx: RunContext[SerniaDeps], query: str, max_results: int = 20) -> str:
    raw_results = await gmail_search(query, max_results)
    if len(raw_results) > SUMMARIZATION_CHAR_THRESHOLD:
        result = await summarization_agent.run(
            f"Summarize these email search results:\n{raw_results}",
            usage=ctx.usage,
        )
        return result.output.model_dump_json()
    return ToolResultSummary(
        format="verbatim", item_count=len(raw_results),
        returned_count=len(raw_results), content=raw_results
    ).model_dump_json()
```

---

## Triggers & Entry Points

### 1. Human-Initiated Conversation (Primary)

See [Human Interaction Modalities](#human-interaction-modalities) below.

### 2. Incoming SMS Trigger (`triggers/sms_trigger.py`)

Extends the existing OpenPhone webhook handler at `POST /api/open_phone/webhook`.

**Flow**:
1. Existing webhook fires, persists `OpenPhoneEvent` as before
2. New: After persistence, invoke Sernia agent in background task
3. Agent decides what to do (update memory, respond, escalate, nothing)
4. **Does NOT replace `escalate.py`** — runs alongside it for now

```python
# In open_phone/routes.py, add to webhook handler:
async def handle_incoming_sms(event: OpenPhoneEvent):
    # Existing escalation logic (unchanged)
    await escalate(event)

    # New: trigger Sernia agent (background)
    background_tasks.add_task(
        trigger_sernia_from_sms,
        phone_number=event.from_number,
        message_text=event.message_text,
        conversation_id=f"sms:{event.from_number}",
    )
```

### 3. Scheduled Email Check (`triggers/email_scheduler.py`)

Uses APScheduler (not Pub/Sub — too noisy).

**Flow**:
1. Cron job runs every N minutes (configurable, e.g. every 15 min)
2. Fetches unread/new emails from monitored inboxes
3. For each relevant email thread, invokes agent with `modality="email"`
4. Agent decides: reply, update memory, create ClickUp task, or ignore

```python
def register_email_check_job():
    scheduler = get_scheduler()
    scheduler.add_job(
        check_emails,
        trigger=CronTrigger(minute="*/15"),
        id="sernia_email_check",
        replace_existing=True,
    )
```

---

## Human Interaction Modalities

### SMS (Primary)

- **Thread model**: 1 phone number = 1 long-running conversation
- **Conversation ID**: `sms:{e164_phone_number}`
- **Compaction**: Same as all modalities — at ~85% of context window
- **Character awareness**: Agent should know SMS has practical length limits
- **Incoming**: OpenPhone webhook trigger
- **Outgoing**: Quo MCP or `send_message()` tool
- **Phone number**: Uses the Alert Robot number (will be renamed soon)

### Email (Secondary)

- **Thread model**: 1 Gmail thread = 1 conversation
- **Conversation ID**: `email:{gmail_thread_id}`
- **Compaction**: Same as all modalities — at ~85% of context window
- **Tone**: More formal than SMS
- **Incoming**: APScheduler periodic check
- **Outgoing**: `send_email()` tool via delegated service account

### Web Chat

- **Thread model**: Standard — frontend-managed threads (typical conversation interface)
- **Conversation ID**: UUID generated by React Router frontend
- **Compaction**: Same as all modalities — at ~85% of context window
- **Streaming**: Vercel AI SDK Data Stream Protocol (same as existing agents)
- **Endpoint**: `POST /api/ai-sernia/chat` with streaming response

---

## Database Changes

### `agent_conversations` Table Additions

```sql
ALTER TABLE agent_conversations ADD COLUMN modality VARCHAR DEFAULT 'web_chat';
ALTER TABLE agent_conversations ADD COLUMN contact_identifier VARCHAR;
ALTER TABLE agent_conversations ADD COLUMN estimated_tokens INTEGER DEFAULT 0;

CREATE INDEX ix_agent_conv_modality ON agent_conversations (modality);
CREATE INDEX ix_agent_conv_contact ON agent_conversations (contact_identifier);
```

| Column | Type | Purpose |
|--------|------|---------|
| `modality` | String | `"sms"`, `"email"`, `"web_chat"` — enables modality-specific queries |
| `contact_identifier` | String, nullable | Phone number (SMS) or email thread ID (email) — enables quick lookups |
| `estimated_tokens` | Integer | Running token count — avoids re-parsing all messages to check compaction threshold |

### Migration

Create Alembic migration: `cd api && uv run alembic revision --autogenerate -m "add modality and contact_identifier to agent_conversations"`

---

## Implementation Phases

### Phase 1: Foundation — ✅ Complete
- [x] Set up directory structure and `__init__.py` files
- [x] Create `config.py` with allowed domains, thresholds
- [x] Create `deps.py` with `SerniaDeps`
- [x] Create `agent.py` with basic agent (Claude Sonnet 4.5, static instructions, `WebSearchTool`, `WebFetchTool`)
- [x] Create `routes.py` with web chat endpoint (streaming via `VercelAIAdapter`)
- [x] Set up `.workspace/` directory with gitignore and seed content
- [x] Wire agent into `api/index.py` route registration (via `ai/routes.py` → `ai_sernia/routes.py`)
- [x] Added `DeferredToolRequests` output type for HITL approval flow
- [x] Added conversation CRUD endpoints (get, list, delete, approve)
- [ ] ~~Create Alembic migration for new columns~~ (deferred — columns not yet needed for web chat)

**Implementation notes**: Conversation persistence reuses existing `agent_conversations` table and utilities from `api/src/ai/models.py`. HITL approval flow reuses `api/src/ai/hitl_utils.py`. No new DB columns needed yet.

### Phase 2: Memory System — ✅ Complete
- [x] Create `memory/__init__.py` with `resolve_safe_path`, `ALLOWED_SUFFIXES`, `ensure_workspace_dirs`
- [x] Create `memory/toolset.py` with custom `FunctionToolset` (read_file, write_file, append_to_file, list_directory)
- [x] Create `instructions.py` with MEMORY.md injection, daily notes, modality guidance, and datetime context
- [x] Agent can read/write memory files via workspace tools

**Deviation from plan**: Used a custom `FunctionToolset` instead of `pydantic-ai-filesystem-sandbox`. Simpler, no extra dependency, same safety guarantees. `pydantic-ai-skills` deferred until SOPs are created.

### Phase 3: Workspace Admin Tool — ✅ Complete
- [x] Create `workspace_admin/routes.py` with 6 CRUD endpoints (ls, read, write, mkdir, delete, download)
- [x] Auth via `SerniaUser` dependency (Clerk + `@serniacapital.com` email gate)
- [x] Extract shared `resolve_safe_path` to `memory/__init__.py` for reuse by both agent tools and admin API
- [x] Build frontend workspace explorer page (`workspace.tsx`) with path bar, directory browsing, file view/edit
- [x] Implement create file, create folder, delete (with `AlertDialog` confirmation), download
- [x] Add "AI Workspace" link to sidebar (gated behind `isSerniaCapitalUser`)

**Deviation from plan**: Single page at `workspace.tsx` with state-driven navigation instead of `sernia/workspace.tsx` + `sernia/workspace.$path.tsx` nested routes. Simpler and avoids dynamic route complexity. Omitted `delete-all` endpoint (too dangerous).

### Phase 4: Core Tools
- [ ] Evaluate Quo MCP (test script with `MCPServerSSE`)
- [ ] Implement `quo_tools.py` (MCP or custom FunctionToolset)
- [ ] Implement `google_tools.py` (wrapping existing services)
- [ ] Implement `clickup_tools.py` (wrapping existing service)
- [ ] Implement `db_search_tools.py` (conversation + SMS history search)
- [ ] Test: agent can send SMS, search email, list tasks

### Phase 5: Sub-Agents & Compaction
- [ ] Implement `history_compactor.py`
- [ ] Implement `history_processor.py` with token-aware compaction (~85% threshold)
- [ ] Implement `summarization.py` for large tool results
- [ ] Wire summarization into tools that return large data
- [ ] Test: long conversation compacts correctly, large results get summarized

### Phase 6: Triggers
- [ ] Implement `sms_trigger.py` (extend OpenPhone webhook)
- [ ] Implement `email_scheduler.py` (APScheduler email check)
- [ ] Register email check job in `api/index.py` lifespan
- [ ] Test: incoming SMS triggers agent, email check runs on schedule

### Phase 7: Frontend Chat
- [ ] Build web chat UI in React Router app
- [ ] Conversation list, thread view, streaming messages
- [ ] Connect to `POST /api/ai-sernia/chat`

---

## Open Questions

1. **Quo MCP auth**: How does the API key get passed during SSE handshake? Need to test with `MCPServerSSE` custom headers.
2. **Email monitoring scope**: Which inboxes/labels should the scheduled check monitor? All unread? Specific labels?
3. **Agent autonomy for SMS responses**: Should the agent auto-reply to tenant SMS, or always require human approval (HITL pattern from existing `hitl_sms_agent.py`)?
4. ~~**Railway volume path**~~: Resolved — uses `WORKSPACE_PATH` env var with fallback to repo-relative `.workspace/`. Railway volume mount path configurable via environment.
5. **ClickUp scope**: Which workspaces/views should the agent have access to? Just Peppino's view, or broader?
6. **Escalation coexistence**: How long do we run `escalate.py` alongside the new agent? What's the handoff plan?
7. **DB migration timing**: The `modality`, `contact_identifier`, and `estimated_tokens` columns haven't been added yet. These are only needed when SMS/email triggers are built (Phase 6). Create the migration then.

---

## Reference: Existing Code to Reuse

| Existing Code | Location | How We Use It |
|--------------|----------|---------------|
| Conversation persistence | `api/src/ai/models.py` | `save_agent_conversation()`, `get_conversation_messages()`, etc. |
| Agent run patching | `api/src/ai/agent_run_patching.py` | `patch_run_with_persistence()` for auto-save after runs |
| OpenPhone send | `api/src/open_phone/service.py` | `send_message()` — fallback if MCP doesn't work |
| OpenPhone webhook | `api/src/open_phone/routes.py` | Extend with SMS trigger |
| Gmail send | `api/src/google/gmail/service.py` | `send_email()` via delegated credentials |
| Calendar | `api/src/google/calendar/service.py` | `create_calendar_event()` |
| Service account auth | `api/src/google/common/service_account_auth.py` | `get_delegated_credentials()` |
| ClickUp tasks | `api/src/clickup/service.py` | `get_peppino_view_tasks()` + expand |
| APScheduler | `api/src/apscheduler_service/service.py` | `get_scheduler()` for email check job |
| HITL pattern | `api/src/ai/hitl_agents/hitl_sms_agent.py` | Reference for approval flow if needed |
| Vercel AI streaming | `api/src/ai/chat_emilio/routes.py` | `VercelAIAdapter` pattern for web chat |

## Reference: Key PydanticAI Patterns

| Pattern | Use For |
|---------|---------|
| `@agent.instructions` (not `system_prompt`) | Dynamic context injection — always re-evaluated, even with message_history |
| `FunctionToolset` | Grouping related tools (quo_toolset, google_toolset, etc.) |
| `builtin_tools=[WebSearchTool(), WebFetchTool()]` | Web research with domain filtering (Anthropic-only feature) |
| `history_processors=[fn]` | Token-aware compaction before each model call |
| `MCPServerSSE` | Connecting to Quo MCP if evaluation passes |
| `output_type=ToolResultSummary` | Structured sub-agent output for summarization |
| `usage=ctx.usage` | Share token tracking between parent and sub-agents |
| `RunContext[SerniaDeps]` | Access deps in tools and instructions |
