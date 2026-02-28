# Sernia AI Agent — Architecture Plan

> **Last Updated**: 2026-02-28

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
| **LLM (main agent)** | Claude Sonnet 4.6 (`anthropic:claude-sonnet-4-6`) | Required for `WebSearchTool` (with `allowed_domains`) and `WebFetchTool` — both are Anthropic-only features in PydanticAI. |
| **LLM (sub-agents)** | Claude Haiku 4.5 (`anthropic:claude-haiku-4-5-20251001`) | Cost savings for summarization/compaction work. Anthropic preferred for consistency. |
| **Framework** | PydanticAI (latest stable API) | Already in use. Uses `instructions` list pattern, `FileSystemToolset`, `builtin_tools`, `history_processors`. |
| **Code location** | `api/src/sernia_ai/` | Dedicated module. Imports from existing services (`open_phone/`, `google/`, `clickup/`, `ai_demos/models.py`). |
| **Conversation storage** | Existing `agent_conversations` table | Add columns for modality and contact identifier. Reuse existing persistence utilities from `api/src/ai_demos/models.py`. |
| **Quo/OpenPhone** | FastMCP OpenAPI bridge + custom guards | OpenPhone REST API spec converted to MCP tools via FastMCP. Custom `send_message` with contact verification + from-phone enforcement. Custom `search_contacts` with fuzzy matching. |
| **Memory storage** | `pydantic-ai-filesystem-sandbox` `FileSystemToolset` | Sandboxed filesystem with `Mount` config (`.md`, `.txt`, `.json` suffixes, `rw` mode). Plus a custom `search_files` tool for text search. `.workspace/` backed by git repo (`sernia-knowledge`). |
| **Git sync** | `memory/git_sync.py` | `.workspace/` backed by `EmilioEsposito/sernia-knowledge` GitHub repo via PAT. Clone/pull on startup, commit+push after each agent turn. |
| **Skills/SOPs** | Deferred | `pydantic-ai-skills` not yet needed. Skills directory exists in workspace structure; toolset can be added later when SOPs are created. |
| **Web research** | PydanticAI `WebSearchTool` + `WebFetchTool` | Builtin tools with `allowed_domains` for safe web access. Domain allowlist in easy-to-edit config file. |

---

## Directory Structure

```
api/src/sernia_ai/
├── __init__.py
├── agent.py                 # ✅ Main Sernia agent definition + search_files tool
├── deps.py                  # ✅ SerniaDeps dataclass
├── config.py                # ✅ Allowed domains, thresholds, tunables
├── instructions.py          # ✅ Static + dynamic instructions (all in one file)
├── routes.py                # ✅ FastAPI routes (web chat, conversations, approvals, admin)
│
├── tools/                   # ✅ Phase 4
│   ├── __init__.py
│   ├── openphone_tools.py   # ✅ FastMCP OpenAPI bridge + custom send_message, search_contacts
│   ├── google_tools.py      # ✅ Gmail, Calendar, Drive, Docs, Sheets, PDFs
│   ├── clickup_tools.py     # ✅ List browsing, task search, CRUD (create/update/delete gated)
│   ├── db_search_tools.py   # ✅ Search agent_conversations
│   └── code_tools.py        # ✅ Secure Python sandbox (pydantic-monty)
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
└── memory/
    ├── __init__.py          # ✅ ensure_workspace_dirs, seed content, .gitkeep framework
    └── git_sync.py          # ✅ Git-backed sync (clone/pull/commit/push via PAT)
```

**Frontend** (in React Router app):
```
apps/web-react-router/app/routes/
├── sernia-chat.tsx          # ✅ Chat UI + System Instructions admin tab
└── workspace.tsx            # ✅ Single-page file explorer (state-driven nav)
```

**Workspace directory** (git-backed via `sernia-knowledge` repo):
```
.workspace/
├── MEMORY.md                           # Long-term memory (injected every conversation)
├── daily_notes/
│   └── YYYY-MM-DD_<short-desc>.md      # One file per topic per day
├── areas/
│   └── <topic>.md                      # Deep topic knowledge (properties, tenants, etc.)
└── skills/
    └── <skill_name>/
        ├── SKILL.md                    # SOP instructions
        └── resources/                  # Reference docs
```

---

## Agent Architecture

### Main Agent (`agent.py`) — Implemented

```python
sernia_agent = Agent(
    MAIN_AGENT_MODEL,                # anthropic:claude-sonnet-4-6
    deps_type=SerniaDeps,
    instructions=[STATIC_INSTRUCTIONS, *DYNAMIC_INSTRUCTIONS],
    output_type=[str, DeferredToolRequests],  # HITL foundation
    builtin_tools=_build_builtin_tools(),     # WebSearchTool + WebFetchTool (if Anthropic)
    toolsets=[
        filesystem_toolset,    # pydantic-ai-filesystem-sandbox
        quo_toolset,           # OpenPhone (FastMCP + custom guards)
        google_toolset,        # Gmail, Calendar, Drive, Docs, Sheets, PDFs
        clickup_toolset,       # ClickUp lists, tasks, CRUD
        db_search_toolset,     # Conversation history search
        code_toolset,          # Secure Python sandbox (pydantic-monty)
    ],
    instrument=True,
    name=AGENT_NAME,
)

@sernia_agent.tool
async def search_files(ctx, query, glob_pattern="**/*.md") -> str:
    """Case-insensitive text search across workspace files."""
    ...
```

**Instructions pattern**: All instructions live in `instructions.py`. Static instructions are a plain string, dynamic instructions are standalone functions that take `RunContext[SerniaDeps]` and return a string. Both are passed as `instructions=[STATIC_INSTRUCTIONS, *DYNAMIC_INSTRUCTIONS]` — no decorator pattern.

**File tools**: Provided by `pydantic-ai-filesystem-sandbox` `FileSystemToolset` with a `Sandbox` mounting `.workspace/` at `/workspace/` in `rw` mode. Gives the agent: `read_file`, `write_file`, `edit_file`, `list_files`, `delete_file`, `move_file`, `copy_file`. The custom `search_files` tool fills the gap (no grep in the sandbox toolset).

**Note**: `history_processors` not yet wired (Phase 5). `DeferredToolRequests` output type enables HITL approval flow.

### Configuration (`config.py`) — Implemented

Easy-to-tweak file for domains, thresholds, and tunables:

```python
WEB_SEARCH_ALLOWED_DOMAINS: list[str] = [
    "zillow.com", "redfin.com", "apartments.com",
    "rentometer.com", "clickup.com", "serniacapital.com",
]

TOKEN_COMPACTION_THRESHOLD = 170_000   # ~85% of 200k context window
SUMMARIZATION_CHAR_THRESHOLD = 10_000
MAIN_AGENT_MODEL = "anthropic:claude-sonnet-4-6"
SUB_AGENT_MODEL = "anthropic:claude-haiku-4-5-20251001"
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

All instructions are in `instructions.py` as standalone functions (no decorator). Passed to the agent as `instructions=[STATIC_INSTRUCTIONS, *DYNAMIC_INSTRUCTIONS]`.

1. **`inject_context`** — Current datetime (ET) + user name + modality
2. **`inject_memory`** — Reads `.workspace/MEMORY.md` (capped at 5k chars)
3. **`inject_filetree`** — ASCII tree of the entire `.workspace/` directory (capped at 3k chars, hides `.git` and `.gitkeep`)
4. **`inject_modality_guidance`** — SMS: short/direct, email: formal, web_chat: conversational

### Auth — Implemented

Router-level auth gating via `APIRouter(dependencies=[Depends(_sernia_gate)])`. The `_sernia_gate` dependency verifies Clerk JWT + `@serniacapital.com` email, then stashes the user on `request.state.sernia_user`. Individual endpoints use `_get_sernia_user(request)` to retrieve the verified user.

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

### Web Research (builtin tools) — Implemented

The main agent has PydanticAI's `WebSearchTool` and `WebFetchTool` as builtin tools. Both are configured with `allowed_domains` from `config.py` so the agent can only search/fetch from approved sites.

```python
builtin_tools=[
    WebSearchTool(allowed_domains=WEB_SEARCH_ALLOWED_DOMAINS),
    WebFetchTool(allowed_domains=WEB_SEARCH_ALLOWED_DOMAINS),
]
```

**Why Anthropic is required**: `allowed_domains` on `WebSearchTool` is only supported by Anthropic and Groq models. `WebFetchTool` only works with Anthropic and Google. OpenAI models silently ignore domain filtering and don't support `WebFetchTool` at all.

### Quo / OpenPhone Tools (`openphone_tools.py`) — ✅ Implemented

**Architecture**: FastMCP OpenAPI bridge + custom FunctionToolset layer.

Note: OpenPhone was renamed to Quo recently, but this repo still refers to it as OpenPhone. Don't rename existing code.

1. **FastMCP OpenAPI bridge**: Fetches the public OpenPhone REST API spec, patches schema issues, trims descriptions, and exposes a curated set of MCP tools (messages, contacts, calls, recordings, transcripts, conversations).
2. **Custom toolset layer**: Wraps FastMCP with approval gates on write operations, plus two custom tools:
   - `search_contacts` — Fuzzy contact search (uses `fuzzy_json` utility, 5-min cached contact list)
   - `send_message` (requires_approval) — Deterministic guards: recipient must exist in contacts, from-phone auto-selected (internal line for Sernia Capital contacts, shared external line otherwise)

| Tool | Type | Approval |
|------|------|----------|
| `search_contacts` | Custom | No |
| `send_message` | Custom | **Yes** |
| `listMessages_v1` / `getMessageById_v1` | MCP | No |
| `getContactById_v1` | MCP | No |
| `createContact_v1` / `updateContactById_v1` / `deleteContact_v1` | MCP | **Yes** |
| `listCalls_v1` / `getCallById_v1` | MCP | No |
| `getCallSummary_v1` / `getCallTranscript_v1` | MCP | No |
| `listConversations_v1` | MCP | No |

### Google Tools (`google_tools.py`) — ✅ Implemented

`FunctionToolset` wrapping existing Google services via delegated service account credentials.

| Tool | Approval | Description |
|------|----------|-------------|
| `send_email` | **Yes** | Send email via Gmail |
| `search_emails` | No | Gmail search with full query syntax |
| `read_email` | No | Read full email content by message ID |
| `list_calendar_events` | No | Upcoming Google Calendar events |
| `create_calendar_event` | **Yes** | Create calendar event |
| `search_drive` | No | Search Google Drive files |
| `read_google_doc` | No | Read Google Doc text content |
| `read_google_sheet` | No | Read Google Sheet data (supports sheet name + range) |
| `read_drive_pdf` | No | Extract text from Drive PDFs |

### ClickUp Tools (`clickup_tools.py`) — ✅ Implemented

`FunctionToolset` using ClickUp REST API v2 directly. Shared `_clickup_request` / `_clickup_request_params` helpers for DRY HTTP calls.

| Tool | Approval | Description |
|------|----------|-------------|
| `list_clickup_lists` | No | Traverse spaces → folders → lists with IDs and task counts |
| `get_tasks` | No | Get tasks from a list or view (auto-detects numeric list ID vs view ID) |
| `search_tasks` | No | Filtered team tasks endpoint with server-side filters + optional fuzzy text query |
| `create_task` | **Yes** | Create task in a list |
| `update_task` | **Yes** | Update task name, status, priority, due date |
| `delete_task` | **Yes** | Delete a task |

### Database Search Tools (`db_search_tools.py`) — ✅ Implemented (partial)

Database-backed search across internal tables. Named `db_search_tools` to distinguish from Google Drive search, email search, etc. which live in their respective toolset files.

| Tool | Description | Status |
|------|-------------|--------|
| `search_conversations` | Full-text search across `agent_conversations.messages` JSON. Returns conversation snippets with metadata (who, when, modality). | ✅ |
| `search_sms_history` | Full-text search across `open_phone_messages` table. For historical SMS context. | 🔲 Deferred |

### Code Execution Tools (`code_tools.py`) — ✅ Implemented

| Tool | Description |
|------|-------------|
| `run_python` | Secure Python sandbox via pydantic-monty. Exposes helper functions for datetime, JSON, regex, and math operations. No filesystem or network access. |

---

## Memory System

### Implemented: `pydantic-ai-filesystem-sandbox`

The agent's file tools come from `pydantic-ai-filesystem-sandbox` `FileSystemToolset` with a `Sandbox` that mounts `.workspace/` at `/workspace/` in `rw` mode. Suffix allowlist: `.md`, `.txt`, `.json`.

**Agent file tools** (from `FileSystemToolset`):
| Tool | Description |
|------|-------------|
| `read_file(path)` | Read a file |
| `write_file(path, content)` | Create or overwrite a file |
| `edit_file(path, ...)` | Edit a file in place |
| `list_files(path)` | List directory contents |
| `delete_file(path)` | Delete a file |
| `move_file(src, dst)` | Move/rename a file |
| `copy_file(src, dst)` | Copy a file |

**Custom tool** (`@sernia_agent.tool`):
| Tool | Description |
|------|-------------|
| `search_files(query, glob_pattern)` | Case-insensitive text search across workspace files. Returns matching lines with file paths. |

### Git-Backed Sync (`memory/git_sync.py`) — Implemented

`.workspace/` is backed by the `EmilioEsposito/sernia-knowledge` GitHub repo:
- **On startup** (`ensure_repo`): Clone (if empty) or pull (if existing). Falls back to local-only if no PAT set.
- **After each agent turn** (`commit_and_push`): Stage all changes, commit with file summary message, push. Handles merge conflicts by committing conflict markers for the agent to resolve.
- **Requires**: `GITHUB_EMILIO_PERSONAL_WRITE_PAT` env var + `git` binary (installed in Dockerfile).

### Workspace Seeding (`memory/__init__.py`) — Implemented

`ensure_workspace_dirs()` creates the directory structure and seeds initial content:
- `MEMORY.md` — seeded with basic structure (key people, properties, notes)
- `.gitkeep` files with descriptive comments explaining naming conventions for `daily_notes/`, `areas/`, `skills/`

### Skills Configuration (Deferred)

The `pydantic-ai-skills` package is not yet installed. The `skills/` directory exists in the workspace structure and can be wired up later when SOPs are created.

### Memory Tiers

| Tier | Path | Purpose | Injected? |
|------|------|---------|-----------|
| **Long-term** | `/workspace/MEMORY.md` | Patterns, rules, key facts | Yes — every conversation |
| **Filetree** | Entire `.workspace/` | ASCII tree of all files | Yes — every conversation |
| **Daily Notes** | `/workspace/daily_notes/YYYY-MM-DD_<desc>.md` | Activity logs, business events | No — loaded on demand via file tools |
| **Areas** | `/workspace/areas/<topic>.md` | Organized knowledge (agent decides structure) | No — loaded on demand via file tools |
| **Skills** | `/workspace/skills/<name>/SKILL.md` | SOPs, business procedures | No — loaded on demand via file tools |

---

## Workspace Admin Tool — Implemented (Phase 3)

A backend API + frontend for humans to manually browse, create, edit, and delete files in the `.workspace/` directory. Gated to `@serniacapital.com` users via router-level auth.

### Backend (`workspace_admin/routes.py`) — Implemented

FastAPI sub-router with `prefix="/workspace"`, included in the Sernia router. All endpoints use the router-level `_sernia_gate` dependency for auth. Endpoints are at `/api/sernia-ai/workspace/*`.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/ls?path=` | GET | List directory contents (empty path = workspace root) |
| `/read?path=` | GET | Read file content |
| `/write` | POST | Create or overwrite a file (body: `{path, content}`) |
| `/mkdir` | POST | Create a directory (body: `{path}`) |
| `/delete?path=` | DELETE | Delete a file or empty directory |
| `/download?path=` | GET | Download file as attachment (`FileResponse`) |

### Frontend (`workspace.tsx`) — Implemented

Single-page file explorer at `/workspace` in the React Router app. Uses state-driven navigation (no nested routes).

- **Path bar** — clickable `.workspace / segment / segment` breadcrumbs (shadcn `Button variant="link"`)
- **Directory view** — entries with `Folder`/`FileText` icons, click to navigate or open
- **File view** — content in `<Textarea>` (read-only by default). Edit/Save buttons toggle editing
- **Actions** — New File, New Folder (inline inputs), Delete (with `AlertDialog` confirmation), Download
- **Auth** — `<AuthGuard>` wrapper, sidebar link gated behind `isSerniaCapitalUser`

### Admin: System Instructions View — Implemented

Admin tab on the Sernia Chat page (`/sernia-chat` → "System Instructions" tab) that shows resolved agent instructions as the model sees them.

- Backend endpoint: `GET /api/sernia-ai/admin/system-instructions` — calls the actual instruction functions with a mock context
- Supports query params for mocking: `modality` (web_chat/sms/email) and `user_name`
- Frontend shows each instruction section in a labeled `<pre>` block with refresh, modality toggle buttons, and user name override input

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

---

## Triggers & Entry Points

### 1. Human-Initiated Conversation (Primary)

See [Human Interaction Modalities](#human-interaction-modalities) below.

### 2. Incoming SMS Trigger (`triggers/sms_trigger.py`)

Extends the existing OpenPhone webhook handler at `POST /api/open-phone/webhook`.

**Flow**:
1. Existing webhook fires, persists `OpenPhoneEvent` as before
2. New: After persistence, invoke Sernia agent in background task
3. Agent decides what to do (update memory, respond, escalate, nothing)
4. **Does NOT replace `escalate.py`** — runs alongside it for now

### 3. Scheduled Email Check (`triggers/email_scheduler.py`)

Uses APScheduler (not Pub/Sub — too noisy).

**Flow**:
1. Cron job runs every N minutes (configurable, e.g. every 15 min)
2. Fetches unread/new emails from monitored inboxes
3. For each relevant email thread, invokes agent with `modality="email"`
4. Agent decides: reply, update memory, create ClickUp task, or ignore

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

### Web Chat — Implemented

- **Thread model**: Standard — frontend-managed threads (typical conversation interface)
- **Conversation ID**: UUID generated by React Router frontend
- **Compaction**: Same as all modalities — at ~85% of context window
- **Streaming**: Vercel AI SDK Data Stream Protocol (same as existing agents)
- **Endpoint**: `POST /api/sernia-ai/chat` with streaming response
- **Frontend**: `sernia-chat.tsx` — key-based remount pattern (outer `SerniaChatPage` manages conversation selection, inner `ChatView` keyed by `conversationId` for clean state)
- **Features**: Conversation history sidebar, suggested prompts, HITL tool approval cards, System Instructions admin tab

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

Create Alembic migration when SMS/email triggers are built (Phase 6): `cd api && uv run alembic revision --autogenerate -m "add modality and contact_identifier to agent_conversations"`

---

## Implementation Phases

### Phase 1: Foundation — ✅ Complete
- [x] Set up directory structure and `__init__.py` files
- [x] Create `config.py` with allowed domains, thresholds
- [x] Create `deps.py` with `SerniaDeps`
- [x] Create `agent.py` with basic agent (Claude Sonnet 4.5, `WebSearchTool`, `WebFetchTool`)
- [x] Create `routes.py` with web chat endpoint (streaming via `VercelAIAdapter`)
- [x] Set up `.workspace/` directory with gitignore and seed content
- [x] Wire agent into `api/index.py` route registration
- [x] Added `DeferredToolRequests` output type for HITL approval flow
- [x] Added conversation CRUD endpoints (get, list, delete, approve)

### Phase 2: Memory System — ✅ Complete
- [x] Create `memory/__init__.py` with `ensure_workspace_dirs`, seed content, `.gitkeep` framework
- [x] Create `memory/git_sync.py` for git-backed workspace sync (`sernia-knowledge` repo)
- [x] Set up `pydantic-ai-filesystem-sandbox` `FileSystemToolset` with sandboxed `.workspace/` mount
- [x] Add custom `search_files` tool for case-insensitive text search across workspace files
- [x] Create `instructions.py` with all instructions (static + dynamic: context, memory, filetree, modality)
- [x] Agent can read/write/search memory files via workspace tools
- [x] Install `git` in API Dockerfile for Railway deployment

### Phase 3: Frontend Web Chat — ✅ Complete
- [x] Build web chat UI (`sernia-chat.tsx`) with key-based remount pattern
- [x] Conversation history sidebar, thread switching, delete
- [x] Streaming messages via Vercel AI SDK + `DefaultChatTransport`
- [x] HITL tool approval cards (shared components from `tool-cards.tsx`)
- [x] System Instructions admin tab with mock context controls
- [x] Sidebar entry "Sernia AI" gated behind `isSerniaCapitalUser`

### Phase 4: Workspace Admin Tool — ⚠️ Partially Complete
- [x] ~~Create `workspace_admin/routes.py` with 6 CRUD endpoints~~ (built then removed during refactor — agent manages workspace via file tools instead)
- [x] Router-level auth via `_sernia_gate` dependency (Clerk + `@serniacapital.com` email gate)
- [ ] ~~Build frontend workspace explorer page (`workspace.tsx`)~~ (removed — low priority, agent handles file ops directly)
- [x] Add System Instructions admin tab on Sernia Chat page (shows resolved instructions with mock context controls)

### Phase 5: Core Tools — ✅ Complete
- [x] Implement `openphone_tools.py` (FastMCP OpenAPI bridge + custom guards)
- [x] Implement `google_tools.py` (Gmail, Calendar, Drive, Docs, Sheets, PDFs — 9 tools)
- [x] Implement `clickup_tools.py` (list browsing, search, CRUD — 6 tools)
- [x] Implement `db_search_tools.py` (conversation search; SMS history search deferred)
- [x] Implement `code_tools.py` (secure Python sandbox via pydantic-monty)
- [x] Test: agent can send SMS, search email, list/search/create/update/delete tasks
- [x] Live integration tests for ClickUp and OpenPhone tools (pytest -m live)

### Phase 6: Sub-Agents & Compaction
- [ ] Implement `history_compactor.py`
- [ ] Implement `history_processor.py` with token-aware compaction (~85% threshold)
- [ ] Implement `tool_result_summarization.py` for large tool results. This should be the first line of defense before we compact user/ai messages. Compacting just the tool results preserves the general structure of the conversation history, and better context of the user/ai messages.
- [ ] Wire summarization into tools that return large data
- [ ] Test: long conversation compacts correctly, large results get summarized

### Phase 7: Error Handling and Logging
- [ ] Feedback loop errors. I already get logfire slack messages for errors, so I want them to be very good so that another Claude Code AI can take the error and generate a fix.
- [ ] Tool call errors should be logged as errors (I want to be alerted), but they should NOT break the conversation from continuing. It should give an LLM friendly error message, inform the user, and be able to continue on, while logfire gets the error with full stack trace as normal. Some tool call errors could be unavoidable if the LLM provides the wrong payload, so we'll want to know about those sorts of errors so that maybe we decide how to treat them differently over time (maybe evenutally we downgrade them to warnings or just ignore them if they're unavoidable and the LLM can handle them gracefully, but for now, just make them errors so we get the logfire alert).


### Phase 8: PWA push notifications (ios and android)
- [ ] Implement a way to send push notifications to the user's device.


### Phase 8: Triggers and HITL behaviors per modality

Let's pause here and discuss in greater detail before implementing these.

- [ ] Implement `sms_trigger.py` (extend OpenPhone webhook)
- [ ] Implement `email_scheduler.py` (APScheduler email check)
- [ ] Register email check job in `api/index.py` lifespan
- [ ] Create Alembic migration for modality/contact_identifier columns
- [ ] Test: incoming SMS triggers agent, email check runs on schedule


---

## Open Questions

1. ~~**Quo MCP auth**~~: Resolved — went with FastMCP OpenAPI bridge instead of Quo MCP SSE. Uses `OPEN_PHONE_API_KEY` env var directly.
2. **Email monitoring scope**: Which inboxes/labels should the scheduled check monitor? All unread? Specific labels? All, but maybe with special attention to Zillow email label.
3. ~~**Agent autonomy for SMS responses**~~: Resolved — always require human approval (HITL pattern via `requires_approval=True`).
4. ~~**Railway volume path**~~: Resolved — uses `WORKSPACE_PATH` env var with fallback to repo-relative `.workspace/`.
5. ~~**ClickUp scope**~~: Resolved — agent has access to both Sernia Capital and EmilioSana spaces via `CLICKUP_TEAM_ID`. `list_clickup_lists` lets the agent discover all spaces/folders/lists dynamically.
6. ~~**Escalation coexistence**~~: Resolved — `escalate.py` runs alongside. No Twilio integration in agent yet.
7. ~~**DB migration timing**~~: Resolved — `modality`, `contact_identifier`, and `estimated_tokens` columns already added to `agent_conversations` model.

---

## Reference: Existing Code to Reuse

| Existing Code | Location | How We Use It |
|--------------|----------|---------------|
| Conversation persistence | `api/src/ai_demos/models.py` | `save_agent_conversation()`, `get_conversation_messages()`, etc. |
| Agent run patching | `api/src/ai_demos/agent_run_patching.py` | `patch_run_with_persistence()` for auto-save after runs |
| HITL utilities | `api/src/ai_demos/hitl_utils.py` | Shared approval utilities (used by hitl_agents + sernia_ai) |
| OpenPhone send | `api/src/open_phone/service.py` | `send_message()` — fallback if MCP doesn't work |
| OpenPhone webhook | `api/src/open_phone/routes.py` | Extend with SMS trigger |
| Gmail send | `api/src/google/gmail/service.py` | `send_email()` via delegated credentials |
| Calendar | `api/src/google/calendar/service.py` | `create_calendar_event()` |
| Service account auth | `api/src/google/common/service_account_auth.py` | `get_delegated_credentials()` |
| ClickUp tasks | `api/src/clickup/service.py` | `get_peppino_view_tasks()` + expand |
| APScheduler | `api/src/apscheduler_service/service.py` | `get_scheduler()` for email check job |

## Reference: Key PydanticAI Patterns

| Pattern | Use For |
|---------|---------|
| `instructions=[str, *fns]` list | Static + dynamic instructions — functions take `RunContext[SerniaDeps]`, always re-evaluated |
| `FileSystemToolset` + `Sandbox` | Sandboxed file access with mount config, suffix allowlist |
| `FunctionToolset` | Grouping related tools (quo_toolset, google_toolset, etc.) |
| `builtin_tools=[WebSearchTool(), WebFetchTool()]` | Web research with domain filtering (Anthropic-only feature) |
| `history_processors=[fn]` | Token-aware compaction before each model call |
| `FastMCPToolset` | Bridge OpenAPI specs into PydanticAI toolsets (used for OpenPhone) |
| `output_type=ToolResultSummary` | Structured sub-agent output for summarization |
| `usage=ctx.usage` | Share token tracking between parent and sub-agents |
| `RunContext[SerniaDeps]` | Access deps in tools and instructions |
