"""
Memory system for the Sernia AI agent.

Manages the .workspace/ directory structure for persistent memory.
"""
from pathlib import Path

import logfire

from api.src.sernia_ai.memory.git_sync import ensure_repo

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


GITKEEP_FILES: dict[str, str] = {
    "daily_notes/.gitkeep": (
        "# Daily Notes\n"
        "# Naming: YYYY-MM-DD_<short-desc>.md\n"
        "# Examples:\n"
        "#   2025-06-15_lease-renewals.md\n"
        "#   2025-06-15_maintenance-calls.md\n"
        "# One file per topic per day.\n"
    ),
    "areas/.gitkeep": (
        "# Areas — deep topic knowledge\n"
        "# One file per topic, e.g.:\n"
        "#   areas/properties.md   — addresses, units, lease terms\n"
        "#   areas/tenants.md      — names, contacts, notes\n"
        "#   areas/vendors.md      — plumbers, electricians, etc.\n"
        "#   areas/processes.md    — rent collection, maintenance flow\n"
    ),
    "skills/.gitkeep": (
        "# Skills — learned procedures and playbooks\n"
        "# One folder per skill with a SKILL.md and optional resources/.\n"
        "# Example:\n"
        "#   skills/lease_renewal/SKILL.md\n"
        "#   skills/maintenance_request/SKILL.md\n"
    ),
}


def ensure_workspace_dirs(workspace_path: Path) -> None:
    """
    Create workspace directory structure if missing.

    Structure:
        .workspace/
        |-- MEMORY.md          # Long-term memory (injected every conversation)
        |-- daily_notes/       # YYYY-MM-DD_<short-desc>.md per topic per day
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

    for rel_path, content in GITKEEP_FILES.items():
        gitkeep = workspace_path / rel_path
        if not gitkeep.exists():
            gitkeep.write_text(content)
            logfire.info(f"Seeded {rel_path} at {gitkeep}")

    logfire.info(f"Workspace dirs ensured at {workspace_path}")


async def initialize_workspace(workspace_path: Path) -> None:
    """
    Full workspace initialization: git clone/pull then ensure dirs.

    Called once during FastAPI lifespan startup.
    """
    await ensure_repo(workspace_path)
    ensure_workspace_dirs(workspace_path)
