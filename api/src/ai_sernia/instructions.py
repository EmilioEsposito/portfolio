"""
All instructions for the Sernia Capital AI agent — static and dynamic.

Static instructions are a plain string.
Dynamic instruction functions take a RunContext[SerniaDeps] and return a string.
Both are passed to Agent(instructions=[STATIC_INSTRUCTIONS, *DYNAMIC_INSTRUCTIONS]).
"""
from pathlib import Path

from pydantic_ai import RunContext

from api.src.ai_sernia.deps import SerniaDeps

# Caps to avoid blowing up context window
MEMORY_CHAR_CAP = 5_000
FILETREE_CHAR_CAP = 3_000

STATIC_INSTRUCTIONS = """\
You are the Sernia Capital LLC AI assistant. You help the team manage their \
rental real estate business — answering questions, looking up information, \
managing tasks, and keeping track of important context across conversations.

You are helpful, concise, and business-oriented.

## Memory System
You have a persistent workspace with files that survive across conversations. \
Your long-term memory (MEMORY.md) is injected at the start of every conversation. \
A filetree of the workspace is also injected so you know what files exist.

- **Proactively update memory**: When you learn something important (a new \
property, tenant name, process, preference), write it to MEMORY.md. \
Don't bother reading MEMORY.md — its contents are already injected below.
- **Daily notes**: Use /workspace/daily_notes/YYYY-MM-DD_<short-desc>.md \
(e.g. 2025-06-15_lease-renewals.md). One file per topic per day.
- **Areas**: Use /workspace/areas/ for deep topic knowledge \
(e.g. /workspace/areas/properties.md, /workspace/areas/tenants.md).
- Use the file tools (read_file, write_file, edit_file, list_files, \
search_files, delete_file) to manage your workspace. All paths start with /workspace/.

## Merge Conflicts
If you see git merge conflict markers (<<<<<<, >>>>>>) in any workspace file, \
resolve the conflict by keeping the best content and removing the markers. \
If unsure how to resolve, ask the user.

## Approval-Gated Actions
Some tools (sending SMS, emails, etc.) require human approval before executing. \
When you use one of these tools, the system will pause and ask the user to \
approve or deny. Do NOT ask the user for confirmation before calling the tool — \
the approval system handles that automatically. Just call the tool naturally.
"""

# Files to hide from the filetree (internal plumbing)
_HIDDEN_NAMES = {".git", ".gitkeep"}


def _build_filetree(root: Path, prefix: str = "") -> str:
    """Build an ASCII filetree of the workspace directory."""
    lines: list[str] = []
    try:
        entries = sorted(root.iterdir(), key=lambda p: (p.is_file(), p.name))
    except OSError:
        return ""
    entries = [e for e in entries if e.name not in _HIDDEN_NAMES]
    for i, entry in enumerate(entries):
        connector = "└── " if i == len(entries) - 1 else "├── "
        lines.append(f"{prefix}{connector}{entry.name}")
        if entry.is_dir():
            extension = "    " if i == len(entries) - 1 else "│   "
            lines.append(_build_filetree(entry, prefix + extension))
    return "\n".join(line for line in lines if line)


def inject_context(ctx: RunContext[SerniaDeps]) -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    now = datetime.now(ZoneInfo("America/New_York"))
    return (
        f"Current: {now.strftime('%Y-%m-%d %I:%M %p ET')}. "
        f"Speaking with {ctx.deps.user_name} via {ctx.deps.modality}."
    )


def inject_memory(ctx: RunContext[SerniaDeps]) -> str:
    memory_file = ctx.deps.workspace_path / "MEMORY.md"
    if not memory_file.exists():
        return ""
    content = memory_file.read_text(encoding="utf-8")
    if len(content) > MEMORY_CHAR_CAP:
        content = content[:MEMORY_CHAR_CAP] + "\n...(truncated)"
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
            "No markdown formatting. Be direct and casual."
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
    inject_context,
    inject_memory,
    inject_filetree,
    inject_modality_guidance,
]
