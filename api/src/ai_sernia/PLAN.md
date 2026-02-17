# Sernia Capital LLC AI Agent — Architecture Plan

> **Last Updated**: 2026-02-17

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
| **LLM (sub-agents)** | GPT-5.2 (`openai:gpt-5.2`) | Cost savings for summarization/compaction work. No builtin tool dependency. |
| **Framework** | PydanticAI (latest stable API) | Already in use. Use `instructions` (not `system_prompt`), `FunctionToolset`, `builtin_tools`, `history_processors`. |
| **Code location** | `api/src/ai_sernia/` | New module. Imports from existing services (`open_phone/`, `google/`, `clickup/`, `ai/models.py`). |
| **Conversation storage** | Existing `agent_conversations` table | Add columns for modality and contact identifier. Reuse existing persistence utilities from `api/src/ai/models.py`. |
| **Quo/OpenPhone** | Evaluate MCP first, fallback to custom tools | MCP at `https://mcp.quo.com/sse` has 5 tools (send, bulk send, check messages, call transcripts, create contacts). Beta, SSE transport. See [Quo MCP Evaluation](#quo-mcp-evaluation). |
| **Memory storage** | `pydantic-ai-filesystem-sandbox` | Sandboxed file access. `.workspace/` on localhost (gitignored), Railway volume in production. |
| **Skills/SOPs** | `pydantic-ai-skills` | Progressive disclosure pattern — agent loads skill details only when needed. |
| **Web research** | PydanticAI `WebSearchTool` + `WebFetchTool` | Builtin tools with `allowed_domains` for safe web access. Domain allowlist in easy-to-edit config file. |

---

## Directory Structure

```
api/src/ai_sernia/
├── __init__.py
├── agent.py                 # Main Sernia agent definition
├── deps.py                  # SerniaDeps dataclass
├── config.py                # Allowed domains, thresholds, tunables
├── instructions.py          # Dynamic @agent.instructions functions
├── history_processor.py     # Token-aware compaction via history_processors
├── routes.py                # FastAPI routes (web chat endpoint)
│
├── tools/
│   ├── __init__.py
│   ├── quo_tools.py         # Quo/OpenPhone: send SMS, read messages, transcripts
│   ├── google_tools.py      # Gmail, Calendar, Drive, Docs
│   ├── clickup_tools.py     # Tasks, projects
│   └── db_search_tools.py   # Search agent_conversations + open_phone_messages tables
│
├── sub_agents/
│   ├── __init__.py
│   ├── history_compactor.py # Summarize old messages for compaction
│   └── summarization.py     # Summarize large tool results
│
├── triggers/
│   ├── __init__.py
│   ├── sms_trigger.py       # Extends OpenPhone webhook → agent
│   └── email_scheduler.py   # APScheduler periodic email check → agent
│
├── memory/
│   ├── __init__.py
│   └── sandbox.py           # Workspace sandbox configuration
│
└── workspace_admin/
    ├── __init__.py
    └── routes.py            # Admin API for managing .workspace/ files
```

**Frontend for workspace admin** (in React Router app):
```
apps/web-react-router/app/routes/
└── sernia/
    ├── workspace.tsx        # Workspace file explorer (main page)
    └── workspace.$path.tsx  # Dynamic route for sub-paths (breadcrumb nav)
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

### Main Agent (`agent.py`)

```python
from pydantic_ai import Agent, WebSearchTool, WebFetchTool
from ai_sernia.config import WEB_SEARCH_ALLOWED_DOMAINS

sernia_agent = Agent(
    'anthropic:claude-sonnet-4-5',
    deps_type=SerniaDeps,
    instructions='...',              # Static base instructions
    history_processors=[compact_if_needed],
    toolsets=[
        quo_toolset,                 # or MCPServerSSE if MCP works
        google_toolset,
        clickup_toolset,
        db_search_toolset,
        memory_toolset,              # FileSystemToolset from sandbox
        skills_toolset,              # SkillsToolset from pydantic-ai-skills
    ],
    builtin_tools=[
        WebSearchTool(allowed_domains=WEB_SEARCH_ALLOWED_DOMAINS),
        WebFetchTool(allowed_domains=WEB_SEARCH_ALLOWED_DOMAINS),
    ],
    instrument=True,                 # Logfire tracing
    name='sernia',
)
```

### Configuration (`config.py`)

Easy-to-tweak file for domains, thresholds, and tunables:

```python
# Web search / fetch: only these domains are allowed
WEB_SEARCH_ALLOWED_DOMAINS: list[str] = [
    "zillow.com",
    "redfin.com",
    "realtor.com",
    "apartments.com",
    "clickup.com",
    "serniacapital.com",
    # Add more as needed
]

# Compaction: trigger at ~85% of context window token estimate
TOKEN_COMPACTION_THRESHOLD = 170_000  # ~85% of 200k context window

# Summarization: tool results larger than this get summarized
SUMMARIZATION_CHAR_THRESHOLD = 10_000
```

### Dependencies (`deps.py`)

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

### Dynamic Instructions (`instructions.py`)

Using `@agent.instructions` (always re-evaluated, even with message_history):

1. **MEMORY.md injection** — reads `.workspace/MEMORY.md` and injects contents
2. **Current date/time** — `The current date is 2026-02-17, 3:45 PM ET.`
3. **User identity** — `You are speaking with {user_name} via {modality}.`
4. **Modality context** — SMS: be concise, email: more formal, web chat: standard
5. **Active context** — recent daily notes excerpt (today's file if it exists)

```python
@sernia_agent.instructions
async def inject_memory(ctx: RunContext[SerniaDeps]) -> str:
    memory_path = ctx.deps.workspace_path / "MEMORY.md"
    if memory_path.exists():
        return f"## Your Memory\n{memory_path.read_text()}"
    return ""

@sernia_agent.instructions
def inject_context(ctx: RunContext[SerniaDeps]) -> str:
    now = datetime.now(ZoneInfo("America/New_York"))
    return (
        f"Current: {now.strftime('%Y-%m-%d %I:%M %p ET')}. "
        f"Speaking with {ctx.deps.user_name} via {ctx.deps.modality}."
    )
```

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

### Sandbox Configuration (`memory/sandbox.py`)

```python
from pydantic_ai_filesystem_sandbox import Sandbox, SandboxConfig, Mount, FileSystemToolset

def create_memory_toolset(workspace_path: Path) -> FileSystemToolset:
    config = SandboxConfig(mounts=[
        Mount(
            host_path=str(workspace_path),
            mount_point="/workspace",
            mode="rw",
            suffixes=[".md", ".txt", ".json"],
        ),
    ])
    sandbox = Sandbox(config)
    return FileSystemToolset(sandbox)
```

### Skills Configuration

```python
from pydantic_ai_skills import SkillsToolset

def create_skills_toolset(workspace_path: Path) -> SkillsToolset:
    return SkillsToolset(
        directories=[str(workspace_path / "skills")],
        validate=True,
        max_depth=3,
    )

# Inject available skills into agent instructions
@sernia_agent.instructions
async def inject_skills(ctx: RunContext[SerniaDeps]) -> str | None:
    return await skills_toolset.get_instructions(ctx)
```

### Memory Tiers

| Tier | Path | Purpose | Injected? |
|------|------|---------|-----------|
| **Tacit** | `/workspace/MEMORY.md` | Patterns, rules, principles | Yes — every conversation |
| **Daily Notes** | `/workspace/daily_notes/YYYY-MM-DD.md` | Activity logs, business events | Today's note via instructions |
| **Areas** | `/workspace/areas/<topic>/<file>.md` | Organized knowledge (agent decides structure) | No — loaded on demand via tools |
| **Skills** | `/workspace/skills/<name>/SKILL.md` | SOPs, business procedures | Summary via `SkillsToolset` instructions; full content loaded via `load_skill` tool |

---

## Workspace Admin Tool

A backend API + frontend for humans to manually browse, create, edit, and delete files in the `.workspace/` directory. Gated to `@serniacapital.com` users only.

### Backend (`workspace_admin/routes.py`)

FastAPI router mounted at `/api/ai/sernia/workspace`. All endpoints verify the requesting user's email ends with `@serniacapital.com` (via Clerk auth).

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/ai/sernia/workspace/ls?path=/` | GET | List files and folders at path |
| `/api/ai/sernia/workspace/read?path=/MEMORY.md` | GET | Read file contents |
| `/api/ai/sernia/workspace/write` | POST | Create or overwrite a file (body: `{path, content}`) |
| `/api/ai/sernia/workspace/mkdir` | POST | Create a directory (body: `{path}`) |
| `/api/ai/sernia/workspace/delete` | DELETE | Delete a file or folder (query: `path`) |
| `/api/ai/sernia/workspace/download?path=/areas/tenants/notes.md` | GET | Download a file |
| `/api/ai/sernia/workspace/delete-all` | DELETE | Delete entire workspace (with confirmation) |

**Security**:
- All paths are resolved relative to `.workspace/` root — no path traversal (reject `..`)
- Clerk auth middleware: reject if `user.email` does not end with `@serniacapital.com`

### Frontend (`sernia/workspace.tsx`)

A file explorer page in the React Router app:

- **Breadcrumb navigation** — clickable path segments (e.g. `.workspace / areas / tenants / notes.md`)
- **Back button** — navigates up one directory
- **Directory view** — lists folders and files with icons, click to navigate/open
- **File view** — shows file content in an editable text area with Save button
- **Actions** — Create file, Create folder, Delete, Download
- **Auth gate** — only visible to `@serniacapital.com` users (Clerk)

---

## Sub-Agents

### History Compactor (`sub_agents/history_compactor.py`)

**Purpose**: Summarize old messages when conversation token count reaches ~85% of context window. Compaction is modality-agnostic — same threshold for SMS, email, and web chat.

**Model**: `openai:gpt-5.2` (cost savings — summarization doesn't need builtin tools)

```python
history_compactor = Agent(
    'openai:gpt-5.2',
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

**Model**: `openai:gpt-5.2`

**Pattern**: Wraps tool calls. Returns structured output indicating whether data is verbatim or summarized:

```python
class ToolResultSummary(BaseModel):
    """Structured wrapper so the main agent knows what it's getting."""
    format: Literal["verbatim", "summarized", "truncated"]
    item_count: int                    # Total items in original data
    returned_count: int                # Items included in this response
    content: str                       # The actual data or summary

summarization_agent = Agent(
    'openai:gpt-5.2',
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
- **Endpoint**: `POST /api/ai/sernia/chat` with streaming response

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

### Phase 1: Foundation
- [ ] Set up directory structure and `__init__.py` files
- [ ] Create `config.py` with allowed domains, thresholds
- [ ] Create `deps.py` with `SerniaDeps`
- [ ] Create `agent.py` with basic agent (Claude, static instructions, `WebSearchTool`, `WebFetchTool`, no custom tools yet)
- [ ] Create `routes.py` with web chat endpoint (streaming)
- [ ] Create Alembic migration for new columns
- [ ] Set up `.workspace/` directory with gitignore
- [ ] Wire agent into `api/index.py` route registration
- [ ] Test: web chat round-trip with empty agent (web search should work)

### Phase 2: Memory System
- [ ] Install `pydantic-ai-filesystem-sandbox` and `pydantic-ai-skills`
- [ ] Create `memory/sandbox.py` with sandbox config
- [ ] Create `instructions.py` with MEMORY.md injection and context
- [ ] Test: agent can read/write memory files, skills load correctly

### Phase 3: Workspace Admin Tool
- [ ] Create `workspace_admin/routes.py` with file CRUD endpoints
- [ ] Add `@serniacapital.com` email gate middleware
- [ ] Build frontend workspace explorer page with breadcrumbs
- [ ] Implement file view/edit, create file/folder, delete, download
- [ ] Test: browse, create, edit, delete files via UI

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
- [ ] Connect to `POST /api/ai/sernia/chat`

---

## Open Questions

1. **Quo MCP auth**: How does the API key get passed during SSE handshake? Need to test with `MCPServerSSE` custom headers.
2. **Email monitoring scope**: Which inboxes/labels should the scheduled check monitor? All unread? Specific labels?
3. **Agent autonomy for SMS responses**: Should the agent auto-reply to tenant SMS, or always require human approval (HITL pattern from existing `hitl_sms_agent.py`)?
4. **Railway volume path**: What's the mount path for the `.workspace` volume in production?
5. **ClickUp scope**: Which workspaces/views should the agent have access to? Just Peppino's view, or broader?
6. **Escalation coexistence**: How long do we run `escalate.py` alongside the new agent? What's the handoff plan?

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
