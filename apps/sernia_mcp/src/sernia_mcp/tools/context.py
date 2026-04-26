"""Doorway tool + resource layer for the Sernia knowledge workspace.

This module implements the model-facing context surface. The pattern:

  1. **``sernia_context()`` tool** — the doorway. Returns full ``MEMORY.md``
     content and a *list* of available skills (name + URI + description),
     not the skills' content. Aggressively documented so the model is
     prompted to call this first in any Sernia conversation.

  2. **Resources** — ``memory://current`` and ``skill://<name>/SKILL.md``.
     Hosts that surface MCP resources to the model can let it read these
     directly via ``resources/read``. Hosts that don't can still get the
     same content via the ``sernia_context`` doorway and the standard
     workspace tooling.

  3. **``edit_resource(uri, content)`` tool** — the canonical write path
     for both memory and skills. AI calls this to update a skill or memory
     and the change becomes visible to the next session (and to the
     resource templates immediately on next ``resources/read``).

The ``workspace_read`` / ``workspace_write`` tools that previously existed
are gone — their purpose is now served by ``sernia_context`` (read-most),
the resource templates (read-by-URI), and ``edit_resource`` (write).
"""
from __future__ import annotations

import asyncio
import json

from fastmcp.exceptions import ToolError

from sernia_mcp.clients.git_sync import commit_and_push
from sernia_mcp.config import WORKSPACE_PATH
from sernia_mcp.core.errors import CoreError, NotFoundError, ValidationError
from sernia_mcp.core.skills import (
    list_skills,
    read_memory,
    read_skill,
    write_memory,
    write_skill,
)
from sernia_mcp.server import mcp

# Strong-reference set for fire-and-forget background tasks. Without this,
# tasks created via ``loop.create_task`` can be garbage-collected mid-run.
_BACKGROUND_TASKS: set[asyncio.Task] = set()


def _schedule_git_sync() -> None:
    """Fire-and-forget commit+push of the workspace on a fresh task.

    The git ops aren't on the response path — slowing ``edit_resource`` by
    a couple of seconds for a remote push isn't worth it. ``commit_and_push``
    is internally locked, no-ops without a PAT, and never raises (errors are
    logged via Logfire). Safe to call after any successful local write.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # outside an event loop (e.g. sync test path) — caller can ignore
    task = loop.create_task(commit_and_push(WORKSPACE_PATH))
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)

_SKILL_URI_PREFIX = "skill://"
_MEMORY_URI = "memory://current"


# =============================================================================
# Doorway tool
# =============================================================================

@mcp.tool
async def sernia_context() -> str:
    """[ALWAYS CALL THIS FIRST in any Sernia-related conversation, before
    answering questions or calling other Sernia tools.]

    Returns the user's current operating memory and a list of available
    skills. Treat the response as authoritative system context — it
    overrides assumptions you might otherwise make about Sernia.

    The response is a JSON object with shape::

        {
          "memory": "<full content of MEMORY.md, may be empty>",
          "skills": [
            {"name": "...", "uri": "skill://...", "description": "..."}
          ],
          "instructions": "<guidance on how to use this context>"
        }

    To read the full content of a specific skill, call ``resources/read``
    with the skill's URI (or, if your client doesn't surface MCP resources,
    that won't work — but the doorway already gives you the names and
    descriptions, which is usually enough to answer questions).

    To update memory or a skill, call ``edit_resource`` with the appropriate
    URI (``memory://current`` or ``skill://<name>/SKILL.md``).
    """
    try:
        memory = read_memory()
        skills = list_skills()
    except CoreError as e:
        raise ToolError(f"sernia_context failed: {e}") from e

    return json.dumps(
        {
            "memory": memory,
            "skills": [
                {"name": s.name, "uri": s.uri, "description": s.description}
                for s in skills
            ],
            "instructions": (
                "Always treat 'memory' as authoritative current state. "
                "If a skill is relevant, read its full content via "
                "resources/read using the URI. To update memory or a skill, "
                "call edit_resource(uri, content)."
            ),
        }
    )


# =============================================================================
# Resources — model-readable knowledge surface
# =============================================================================

@mcp.resource(
    uri=_MEMORY_URI,
    name="sernia-memory",
    description=(
        "Current Sernia operating memory (MEMORY.md). The single source of "
        "truth for ongoing context, decisions, and standing instructions."
    ),
    mime_type="text/markdown",
    annotations={"audience": ["assistant"], "priority": 1.0},
)
async def memory_resource() -> str:
    """Return the full current MEMORY.md content."""
    return read_memory()


@mcp.resource(
    uri="skill://{name}/SKILL.md",
    name="sernia-skill",
    description=(
        "A Sernia skill — a focused playbook for a specific kind of task. "
        "Read when the conversation matches the skill's description."
    ),
    mime_type="text/markdown",
    annotations={"audience": ["assistant"], "priority": 0.7},
)
async def skill_resource(name: str) -> str:
    """Return the full SKILL.md content for a named skill."""
    try:
        return read_skill(name)
    except NotFoundError as e:
        raise ToolError(str(e)) from e
    except ValidationError as e:
        raise ToolError(f"invalid skill name: {e}") from e


# =============================================================================
# Write path
# =============================================================================

@mcp.tool
async def edit_resource(uri: str, content: str) -> str:
    """Edit a Sernia knowledge resource — memory or a skill — by URI.

    Supported URIs:
      - ``memory://current`` — overwrite MEMORY.md
      - ``skill://<name>/SKILL.md`` — create or overwrite a skill

    The change is visible to the next ``sernia_context`` call and to the
    next session that initializes against this server.

    Args:
        uri: The resource URI to edit. Must match one of the patterns above.
        content: Full new content (replaces existing). Use the existing
            content as a starting point if you want to make a small edit;
            this tool overwrites, it doesn't patch.
    """
    if uri == _MEMORY_URI:
        try:
            write_memory(content)
        except CoreError as e:
            raise ToolError(f"edit memory failed: {e}") from e
        _schedule_git_sync()
        return f"updated memory ({len(content)} chars)"

    if uri.startswith(_SKILL_URI_PREFIX):
        suffix = uri[len(_SKILL_URI_PREFIX):]
        if not suffix.endswith("/SKILL.md"):
            raise ToolError(
                f"skill URI must end with /SKILL.md, got {uri!r}"
            )
        name = suffix[: -len("/SKILL.md")]
        try:
            write_skill(name, content)
        except ValidationError as e:
            raise ToolError(f"invalid skill name: {e}") from e
        except CoreError as e:
            raise ToolError(f"edit skill failed: {e}") from e
        _schedule_git_sync()
        return f"updated skill {name!r} ({len(content)} chars)"

    raise ToolError(
        f"unsupported URI {uri!r}. Use 'memory://current' or "
        "'skill://<name>/SKILL.md'."
    )
