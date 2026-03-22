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


SEED_SKILLS: dict[str, str] = {
    "create-skill": """\
---
name: create-skill
description: Create a new workspace skill (playbook/procedure) for the Sernia AI agent. Use when the team asks to create, add, or set up a new skill, procedure, or playbook.
---

# Create a New Skill

Use this procedure when the team asks you to create a new skill (playbook, procedure, or domain-specific instruction set).

## What Skills Are

Skills are editable playbooks stored in `/workspace/skills/<skill-name>/SKILL.md`. They are auto-discovered and injected into every conversation, so you and the team can iterate on processes without code deploys.

## Steps to Create a Skill

### 1. Choose a Name

- **Lowercase letters, numbers, and hyphens only** (regex: `^[a-z0-9]+(-[a-z0-9]+)*$`)
- Max 64 characters
- Cannot contain reserved words: "anthropic", "claude"
- Use descriptive, action-oriented names: `tenant-onboarding`, `lease-renewal-checklist`, `maintenance-triage`

### 2. Clarify the Purpose

Before writing, confirm with the user:
- What is this skill for? (e.g., "how to handle a new tenant move-in")
- When should it be used? (e.g., "when a new lease is signed")
- Are there specific steps, rules, or criteria to include?

### 3. Write the SKILL.md

Create the file at `/workspace/skills/<skill-name>/SKILL.md` using `workspace_write`.

**Required format:**

```markdown
---
name: <skill-name>
description: <One-line description of what this skill does and when to use it. Max 1024 chars.>
---

<Skill instructions in markdown. Be directive — use imperative verbs.>
```

**Writing guidelines:**
- **Be directive, not conversational.** Use imperative verbs: "Check the inbox", "Create a task", "Send a follow-up".
- **Keep it under 500 lines.** Move detailed reference material to separate resource files in the same directory if needed.
- **Include decision criteria.** If the skill involves judgment calls, spell out the rules (e.g., "If rent is more than 5 days late, escalate to Emilio").
- **Reference tools by name.** Tell yourself which tools to use (e.g., "Use `clickup_create_task` to create the maintenance ticket").
- **Include examples** of expected inputs/outputs where helpful.

### 4. Verify the Skill

After creating the SKILL.md:
1. Use `list_skills` to confirm the new skill appears (it auto-discovers on every conversation).
2. Use `load_skill` with the skill name to verify the content loads correctly.
3. Tell the user the skill is ready and summarize what it does.

### 5. Optional: Add Resource Files

For skills that need reference data, templates, or detailed documentation, add files alongside SKILL.md:

```
/workspace/skills/<skill-name>/
  SKILL.md              # Main instructions (required)
  template.md           # Template for the agent to follow
  reference.md          # Detailed reference material
  criteria.md           # Decision criteria or checklists
```

Reference these from SKILL.md so you know when to load them:
- "For the full qualification criteria, see `criteria.md` (use `read_skill_resource`)."

Supported resource file types: .md, .json, .yaml, .yml, .csv, .xml, .txt

## Example: Creating a Simple Skill

If the user says: "Create a skill for handling late rent notices"

1. Create `/workspace/skills/late-rent-notice/SKILL.md`:

```markdown
---
name: late-rent-notice
description: Procedure for handling late rent — when to send reminders, escalate, or file notices.
---

# Late Rent Notice Procedure

When rent is reported late or detected during a scheduled check:

1. **Verify the status**: Check the latest payment records or ask Emilio for confirmation.
2. **Day 1-3 late**: Send a friendly reminder SMS to the tenant via `quo_send_sms`.
3. **Day 4-5 late**: Send a firmer reminder and create a ClickUp task via `clickup_create_task` in the maintenance list.
4. **Day 6+ late**: Escalate to Emilio via SMS with full context. Do not send further tenant messages without approval.

Always log the interaction in daily notes: `/workspace/daily_notes/YYYY-MM-DD_late-rent-<unit>.md`
```

2. Verify with `list_skills` and `load_skill`.
3. Tell the user: "Created the `late-rent-notice` skill. It covers the 3-stage escalation process for late rent."

## Updating Existing Skills

To modify an existing skill, use `workspace_edit` on the SKILL.md file. Changes take effect on the next conversation automatically.
""",
}


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
        "# One folder per skill with a SKILL.md (auto-injected every conversation).\n"
        "# Example:\n"
        "#   skills/zillow-auto-reply/SKILL.md\n"
        "#   skills/lease-renewal/SKILL.md\n"
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

    # Seed built-in skills (only if not already present — user edits are preserved)
    for skill_name, skill_content in SEED_SKILLS.items():
        skill_dir = workspace_path / "skills" / skill_name
        skill_file = skill_dir / "SKILL.md"
        if not skill_file.exists():
            skill_dir.mkdir(parents=True, exist_ok=True)
            skill_file.write_text(skill_content)
            logfire.info(f"Seeded skill '{skill_name}' at {skill_file}")

    logfire.info(f"Workspace dirs ensured at {workspace_path}")


async def initialize_workspace(workspace_path: Path) -> None:
    """
    Full workspace initialization: git clone/pull then ensure dirs.

    Called once during FastAPI lifespan startup.
    """
    await ensure_repo(workspace_path)
    ensure_workspace_dirs(workspace_path)
