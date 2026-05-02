"""
All instructions for the Sernia AI agent — static and dynamic.

Static instructions are a plain string.
Dynamic instruction functions take a RunContext[SerniaDeps] and return a string.
Both are passed to Agent(instructions=[STATIC_INSTRUCTIONS, *DYNAMIC_INSTRUCTIONS]).
"""
from datetime import datetime
from pathlib import Path

import logfire
from pydantic_ai import RunContext

from api.src.sernia_ai.deps import SerniaDeps

# Filetree is uncurated — keep capped to avoid noise. MEMORY.md is the agent's
# canonical long-term memory and is injected verbatim; we only log a warning
# if it grows past a sanity threshold so the team can prune.
FILETREE_CHAR_CAP = 3_000
MEMORY_SANITY_WARN_CHARS = 100_000


STATIC_INSTRUCTIONS = """\
You are Sernia Capital LLC's AI intern (Sernia AI Intern). You help the team \
manage their rental real estate business — answering questions, looking up \
information, managing tasks, and keeping track of important context across \
conversations.

You are helpful, concise, and business-oriented.

## Team Context
Emilio Esposito is your manager and the acting CEO of Sernia Capital LLC. \
He handles all approvals and high-level decisions. When in doubt about \
priorities or escalation, default to alerting Emilio.

## Memory System
Your persistent workspace lives at `/workspace/`. Files survive across \
conversations. `MEMORY.md` and a workspace filetree are auto-injected at the \
start of every conversation.

- **MEMORY.md** — proactively update when you learn something important (a \
new property, tenant name, process, preference). Don't `workspace_read` it; \
its contents are already injected.
- **`/workspace/daily_notes/YYYY-MM-DD_<short-desc>.md`** — one file per \
topic per day for ad-hoc notes.
- **`/workspace/areas/<topic>.md`** — deep topic knowledge (properties, \
tenants, vendors, etc.).
- **Skills** — domain playbooks. The skill registry (name + description) is \
auto-injected. Use `list_skills` to browse, `load_skill <name>` to load full \
instructions, `read_skill_resource` for supplementary files. **Never** use \
`workspace_read` to read a skill — use `load_skill`. To CREATE or EDIT a \
skill, write to `/workspace/.claude/skills/<name>/SKILL.md` via \
`workspace_write` / `workspace_edit`. The workspace tools are for editing \
skills, not reading them.
- **General workspace I/O** — `workspace_read`, `workspace_write`, \
`workspace_edit`, `workspace_list_files`, `search_files`, `workspace_delete` \
for everything else under `/workspace/`.

## Merge Conflicts
If you see git merge conflict markers (`<<<<<<`, `>>>>>>`) in any workspace \
file, resolve by keeping the best content and removing the markers. If \
unsure, ask the user.

## Tool Surface
Tool names are prefixed by service: `quo_*` (OpenPhone SMS / contacts / \
calls), `google_*` (Gmail / Calendar / Drive), `clickup_*` (tasks), `db_*` \
(conversation + SMS history search), `workspace_*` (files). Scheduling, \
`run_python`, and the DuckDB analysis tools (`list_datasets`, `load_dataset`, \
`describe_table`, `run_sql`) are unprefixed.

**Each tool's own description has its full call semantics — read those first \
rather than guessing or asking the user.** The bullets below are \
cross-cutting policies that don't belong to any single tool description:

- **Internal vs external routing is automatic.** Communication tools (SMS, \
email, scheduling) auto-detect internal (Sernia Capital LLC / \
@serniacapital.com) vs external recipients and pick the right phone / \
mailbox. Don't think about which line — just pass the recipient.
- **Prefer the shared team number** for general team SMS notifications so \
the whole team sees one thread. Only message a specific person when the \
message is really for them.
- **HITL approval is automatic.** External SMS, external email, mass texts, \
calendar events with external attendees, calendar deletes of events with \
external attendees, contact updates / deletes, and task deletes pause the \
agent for human approval. Internal-only calendar writes (create or delete) \
execute immediately. Do **NOT** ask the user for confirmation before \
calling — the system handles that. Just call the tool naturally.
- **Data-analysis loops**: when a tool returns a large dataset (e.g. \
`google_read_google_sheet`), it auto-saves the full data as a \
conversation-scoped CSV. Use `list_datasets` → `load_dataset` → `run_sql` \
for filtering, aggregation, joins. Data persists across turns in the same \
conversation.
"""

# Files/dirs to hide from the filetree (internal plumbing).
# .mcp.json is for human/Claude-CLI tooling; the agent uses real tools, not
# MCP descriptors.
_HIDDEN_NAMES = {".git", ".gitkeep", ".mcp.json"}

# Directories rendered as "name/ (N entries)" instead of expanded. Matched by
# relative path from the workspace root, so we can collapse only the noisy
# subtree (.claude/skills) while keeping the parent (.claude) navigable.
# - daily_notes: high-volume dated content; agent should search/list, not skim.
# - .claude/skills: skills are discoverable + readable via the ``list_skills``
#   / ``load_skill`` tools — no need to enumerate them as paths.
_COLLAPSED_PATHS = {"daily_notes", ".claude/skills"}


def _count_entries(directory: Path) -> int:
    try:
        return sum(1 for _ in directory.iterdir())
    except OSError:
        return 0


def _build_filetree(root: Path, prefix: str = "", rel_path: str = "") -> str:
    """Build an ASCII filetree of the workspace directory.

    Directories whose path-from-root is in ``_COLLAPSED_PATHS`` render as
    ``name/ (N entries)`` instead of being expanded. Path-based (not name-based)
    matching lets us collapse a noisy subtree without hiding its parent.
    """
    lines: list[str] = []
    try:
        entries = sorted(root.iterdir(), key=lambda p: (p.is_file(), p.name))
    except OSError:
        return ""
    entries = [e for e in entries if e.name not in _HIDDEN_NAMES]
    for i, entry in enumerate(entries):
        connector = "└── " if i == len(entries) - 1 else "├── "
        entry_rel = f"{rel_path}/{entry.name}".lstrip("/")
        if entry.is_dir() and entry_rel in _COLLAPSED_PATHS:
            count = _count_entries(entry)
            lines.append(f"{prefix}{connector}{entry.name}/ ({count} entries)")
            continue
        lines.append(f"{prefix}{connector}{entry.name}")
        if entry.is_dir():
            extension = "    " if i == len(entries) - 1 else "│   "
            lines.append(_build_filetree(entry, prefix + extension, entry_rel))
    return "\n".join(line for line in lines if line)


def format_current_datetime(now: datetime | None = None) -> str:
    """LLM-friendly current datetime: day of week, month, ET and UTC."""
    from zoneinfo import ZoneInfo

    et = ZoneInfo("America/New_York")
    utc = ZoneInfo("UTC")
    now_et = now.astimezone(et) if now is not None else datetime.now(et)
    now_utc = now_et.astimezone(utc)
    return (
        f"{now_et.strftime('%A, %B %d, %Y at %I:%M %p')} ET "
        f"({now_utc.strftime('%I:%M %p')} UTC) | "
        f"ISO 8601: {now_et.isoformat()}"
    )


# Conversations whose first turn has already pulled the workspace. The cache
# is process-local, keyed on conversation_id, and unbounded (~36 bytes per
# entry — fine at any realistic conversation volume). A process restart
# clears it, so each conversation re-pulls once after a redeploy. This
# avoids paying the ~300-500ms pull latency on every follow-up turn.
_pulled_conversation_ids: set[str] = set()


async def refresh_from_remote(ctx: RunContext[SerniaDeps]) -> str:
    """Pull the workspace from GitHub on the **first** turn of each conversation.

    Runs first in ``DYNAMIC_INSTRUCTIONS`` so subsequent ``inject_memory``
    and ``inject_filetree`` calls see the latest state — including edits
    made directly on GitHub or by the ``apps/sernia_mcp`` service.

    Follow-up turns in the same conversation skip the pull (it would have
    re-fetched whatever was already loaded on the first turn, at the cost
    of latency on every user message). Trade-off: long-running conversations
    can drift from the remote until a restart, but conversations are
    typically minutes-long and Sernia restarts daily on dev.

    Returns "" — this dynamic instruction exists for its side effect (the
    pull), not for any system-prompt contribution. ``pull_workspace`` is
    fail-soft, so this never blocks an agent run.
    """
    conv_id = ctx.deps.conversation_id
    if conv_id in _pulled_conversation_ids:
        return ""

    try:
        from api.src.sernia_ai.memory.git_sync import pull_workspace

        await pull_workspace(ctx.deps.workspace_path)
    except Exception:
        logfire.exception("git_sync: refresh_from_remote failed (non-fatal)")
    finally:
        # Mark as pulled even if the pull failed — we don't want to retry
        # on every follow-up turn after a transient failure (it would
        # re-fail and add latency repeatedly). Next conversation gets a
        # fresh attempt; or wait for a restart.
        _pulled_conversation_ids.add(conv_id)
    return ""


def inject_context(ctx: RunContext[SerniaDeps]) -> str:
    from api.src.sernia_ai.config import FRONTEND_BASE_URL

    deeplink = f"{FRONTEND_BASE_URL}/sernia-chat?id={ctx.deps.conversation_id}"
    return (
        f"Current Date and Time: {format_current_datetime()}. "
        f"Speaking with {ctx.deps.user_name} ({ctx.deps.user_email}) via {ctx.deps.modality}. "
        f"When creating calendar events, always include {ctx.deps.user_email} as an attendee unless told otherwise.\n"
        f"This conversation's deeplink: {deeplink}"
    )


def inject_memory(ctx: RunContext[SerniaDeps]) -> str:
    """Inject MEMORY.md verbatim. The system prompt is prompt-cached, so a
    larger MEMORY.md is paid for once per cache window — silently truncating
    the agent's long-term memory was a worse tradeoff than the cache bytes."""
    memory_file = ctx.deps.workspace_path / "MEMORY.md"
    if not memory_file.exists():
        return ""
    content = memory_file.read_text(encoding="utf-8")
    if len(content) > MEMORY_SANITY_WARN_CHARS:
        logfire.warn(
            "MEMORY.md is unusually large — consider pruning",
            chars=len(content),
            threshold=MEMORY_SANITY_WARN_CHARS,
        )
    return f"## Your Long-Term Memory\n{content}"


def inject_filetree(ctx: RunContext[SerniaDeps]) -> str:
    tree = _build_filetree(ctx.deps.workspace_path)
    if not tree:
        return ""
    if len(tree) > FILETREE_CHAR_CAP:
        tree = tree[:FILETREE_CHAR_CAP] + "\n...(truncated)"
    return f"## Workspace Files\n```\n/workspace/\n{tree}\n```"


def inject_modality_guidance(ctx: RunContext[SerniaDeps]) -> str:
    guidance = {
        "sms": (
            "You are communicating via SMS. Keep responses SHORT (1-3 sentences). "
            "No markdown formatting. Be direct and casual. "
            "NOTE: Your conversation history may be trimmed to only recent messages "
            "(last 3 days or last 3 exchanges). If you need earlier context, use "
            "`db_get_contact_sms_history` or `db_search_sms_history`."
        ),
        "email": (
            "You are communicating via email. Use a professional, slightly formal tone. "
            "Structure with paragraphs. Include greetings/closings when appropriate."
        ),
        "web_chat": (
            "You are in a web chat. Use a natural, conversational tone. "
            "Markdown formatting is supported. Be helpful and thorough."
        ),
    }
    return guidance.get(ctx.deps.modality, "")


DYNAMIC_INSTRUCTIONS = [
    refresh_from_remote,  # FIRST: pull from GitHub so memory/filetree see latest
    inject_context,
    inject_memory,
    inject_filetree,
    inject_modality_guidance,
]
