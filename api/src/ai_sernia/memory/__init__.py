"""
Memory system for the Sernia Capital AI agent.

Manages the .workspace/ directory structure for persistent memory.
"""
from pathlib import Path

import logfire

ALLOWED_SUFFIXES = {".md", ".txt", ".json"}


def resolve_safe_path(workspace: Path, relative_path: str) -> Path:
    """
    Resolve a relative path within the workspace, blocking traversal.

    Raises ValueError on invalid paths.
    """
    # Normalise and reject absolute paths
    cleaned = relative_path.strip().lstrip("/")
    if not cleaned:
        raise ValueError("Path cannot be empty")

    resolved = (workspace / cleaned).resolve()

    # Must stay inside workspace
    try:
        resolved.relative_to(workspace.resolve())
    except ValueError:
        raise ValueError(f"Path escapes workspace: {relative_path}")

    # Check suffix
    if resolved.suffix and resolved.suffix not in ALLOWED_SUFFIXES:
        raise ValueError(
            f"File type '{resolved.suffix}' not allowed. "
            f"Allowed: {', '.join(sorted(ALLOWED_SUFFIXES))}"
        )

    return resolved

SEED_MEMORY = """\
# Sernia Capital - Agent Memory

> This file is the agent's long-term memory. It persists across conversations.
> The agent reads this at the start of every conversation and updates it
> when it learns something important.

## Key People

- **Emilio Esposito** - Owner/operator of Sernia Capital LLC

## Properties

(Agent will populate as it learns)

## Important Notes

(Agent will populate as it learns)
"""


def ensure_workspace_dirs(workspace_path: Path) -> None:
    """
    Create workspace directory structure if missing.

    Structure:
        .workspace/
        |-- MEMORY.md          # Long-term memory (injected every conversation)
        |-- daily_notes/       # Per-day scratchpad (YYYY-MM-DD.md)
        |-- areas/             # Topic-specific deep knowledge
        |-- skills/            # Learned procedures and playbooks
    """
    workspace_path.mkdir(parents=True, exist_ok=True)

    for subdir in ("daily_notes", "areas", "skills"):
        (workspace_path / subdir).mkdir(exist_ok=True)

    memory_file = workspace_path / "MEMORY.md"
    if not memory_file.exists():
        memory_file.write_text(SEED_MEMORY)
        logfire.info(f"Seeded MEMORY.md at {memory_file}")

    logfire.info(f"Workspace dirs ensured at {workspace_path}")
