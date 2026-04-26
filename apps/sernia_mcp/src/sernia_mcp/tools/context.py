"""Doorway tool + read/edit/write surface for the Sernia knowledge workspace.

This module implements the model-facing context layer. The shape mirrors
Claude Code's ``Read`` / ``Edit`` / ``Write`` so models that already know
that pattern transfer it cleanly:

  1. **``sernia_context()`` tool** — the doorway. Returns full ``MEMORY.md``
     content and a *list* of available skills (name + URI + description),
     not the skills' content. Aggressively documented so the model is
     prompted to call this first in any Sernia conversation.

  2. **``read_resource(uri)`` tool** — read full content by URI. Mirrors
     Claude Code's ``Read``. Also exposed as MCP resources at
     ``memory://current`` and ``skill://<name>/SKILL.md`` for hosts that
     surface native ``resources/read`` to the model.

  3. **``edit_resource(uri, old_string, new_string, replace_all=False)``
     tool** — string-substitution edit. Mirrors Claude Code's ``Edit``.
     Token-efficient and atomic: ``old_string`` must be unique unless
     ``replace_all=True``, otherwise the call fails. The model only sends
     the bit that's changing, not the whole file.

  4. **``write_resource(uri, content)`` tool** — full overwrite. Mirrors
     Claude Code's ``Write``. Used for new files and large rewrites where
     a string substitution doesn't make sense.

The ``workspace_read`` / ``workspace_write`` tools that previously existed
are gone — their purpose is now served by ``sernia_context``,
``read_resource``, ``edit_resource``, and ``write_resource``.
"""
from __future__ import annotations

import asyncio
import json
from typing import Annotated

from fastmcp.exceptions import ToolError
from pydantic import AliasChoices, Field

import logfire

from sernia_mcp.clients.git_sync import commit_and_push, pull_workspace
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

    The git ops aren't on the response path — slowing edits by a couple of
    seconds for a remote push isn't worth it. ``commit_and_push`` is
    internally locked, no-ops without a PAT, and never raises (errors are
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


def _resolve_uri(uri: str) -> tuple[str, str | None]:
    """Map a Sernia URI to ``(kind, skill_name)``.

    Returns ``("memory", None)`` for ``memory://current`` or
    ``("skill", name)`` for ``skill://<name>/SKILL.md``. Raises ``ToolError``
    for anything else.
    """
    if uri == _MEMORY_URI:
        return ("memory", None)

    if uri.startswith(_SKILL_URI_PREFIX):
        suffix = uri[len(_SKILL_URI_PREFIX):]
        if not suffix.endswith("/SKILL.md"):
            raise ToolError(f"skill URI must end with /SKILL.md, got {uri!r}")
        name = suffix[: -len("/SKILL.md")]
        return ("skill", name)

    raise ToolError(
        f"unsupported URI {uri!r}. Use 'memory://current' or "
        "'skill://<name>/SKILL.md'."
    )


def _read_uri(uri: str) -> str:
    """Read the full content for a Sernia URI."""
    kind, name = _resolve_uri(uri)
    if kind == "memory":
        return read_memory()
    assert name is not None  # narrowing for type checkers
    try:
        return read_skill(name)
    except NotFoundError as e:
        raise ToolError(str(e)) from e
    except ValidationError as e:
        raise ToolError(f"invalid skill name: {e}") from e


def _write_uri(uri: str, content: str) -> str:
    """Overwrite the file for a Sernia URI. Returns a human-readable summary."""
    kind, name = _resolve_uri(uri)
    if kind == "memory":
        try:
            write_memory(content)
        except CoreError as e:
            raise ToolError(f"write memory failed: {e}") from e
        return f"wrote memory ({len(content)} chars)"
    assert name is not None
    try:
        write_skill(name, content)
    except ValidationError as e:
        raise ToolError(f"invalid skill name: {e}") from e
    except CoreError as e:
        raise ToolError(f"write skill failed: {e}") from e
    return f"wrote skill {name!r} ({len(content)} chars)"


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

    To read full skill content, call ``read_resource(uri)`` with the URI
    from the skills list. To edit a small section, call ``edit_resource``
    (string substitution). For large rewrites or new files, ``write_resource``.
    """
    # Pull latest workspace state from remote before reading. Catches edits
    # made directly on GitHub or by the sernia_ai agent since the last sync.
    # Fail-soft — pull_workspace logs warnings but never raises, and the
    # extra try/except here is belt-and-suspenders against unexpected
    # exceptions inside the function (e.g. import-time failures).
    try:
        await pull_workspace(WORKSPACE_PATH)
    except Exception:
        logfire.exception("sernia_context: pre-read pull failed (non-fatal)")

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
                "If a skill's description matches the current task, fetch "
                "its full body via read_resource(uri). For surgical edits "
                "use edit_resource(uri, old_string, new_string); for full "
                "rewrites use write_resource(uri, content)."
            ),
        }
    )


# =============================================================================
# Read
# =============================================================================

@mcp.tool
async def read_resource(uri: str) -> str:
    """Read the full content of a Sernia knowledge resource by URI.

    Use this when ``sernia_context`` returns a skill URI you need to fully
    consult, or when you want fresh ``MEMORY.md`` content.

    Supported URIs:
      - ``memory://current`` — current MEMORY.md content
      - ``skill://<name>/SKILL.md`` — a skill's playbook (use the URI
        returned in the ``skills`` list from ``sernia_context``)

    This is a regular MCP tool because not every host surfaces native
    ``resources/read`` to the model. Functionally equivalent to fetching
    the resource via the protocol — both paths read the same files.
    """
    return _read_uri(uri)


# =============================================================================
# Resources — for hosts that DO surface native resources/read
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
# Edit (string substitution) — mirrors Claude Code's Edit tool
# =============================================================================

@mcp.tool
async def edit_resource(
    uri: str,
    old_string: Annotated[
        str,
        Field(
            description="Exact text in the resource to replace; whitespace-sensitive.",
            validation_alias=AliasChoices("old_string", "old_str"),
        ),
    ],
    new_string: Annotated[
        str,
        Field(
            description="Replacement text. Pass empty string to delete old_string.",
            validation_alias=AliasChoices("new_string", "new_str"),
        ),
    ],
    replace_all: bool = False,
) -> str:
    """Replace ``old_string`` with ``new_string`` inside a Sernia resource.

    Token-efficient surgical edit — the model sends only the changing slice,
    not the whole file. Mirrors Claude Code's ``Edit``: matching is
    whitespace-exact, and the call fails if ``old_string`` isn't unique
    (unless ``replace_all=True``). Read the resource first via
    ``read_resource(uri)`` so the strings you send actually match.

    Supported URIs:
      - ``memory://current`` — edit MEMORY.md
      - ``skill://<name>/SKILL.md`` — edit a skill (must already exist;
        use ``write_resource`` to create a new skill)

    Args:
        uri: The resource URI to edit.
        old_string: Exact text in the resource to replace. Whitespace
            matters. Must be unique unless ``replace_all=True``.
        new_string: Replacement text. Pass ``""`` to delete ``old_string``.
        replace_all: If True, replace every occurrence. Default False (the
            common case): exactly one occurrence required.

    Failure modes:
      - ``old_string`` not found → ToolError
      - ``old_string`` appears multiple times and ``replace_all=False`` →
        ToolError (caller should add surrounding context to disambiguate)
      - ``old_string == new_string`` → ToolError (no-op edit)
    """
    if old_string == new_string:
        raise ToolError("old_string and new_string are identical (no-op edit)")

    current = _read_uri(uri)
    count = current.count(old_string)
    if count == 0:
        raise ToolError(
            f"old_string not found in {uri}. Read the resource first to "
            "confirm exact whitespace + content."
        )
    if count > 1 and not replace_all:
        raise ToolError(
            f"old_string appears {count} times in {uri}; either pass "
            "replace_all=True or extend old_string with surrounding context "
            "to make it unique."
        )

    updated = (
        current.replace(old_string, new_string)
        if replace_all
        else current.replace(old_string, new_string, 1)
    )
    summary = _write_uri(uri, updated)
    _schedule_git_sync()
    occurrences = count if replace_all else 1
    return f"{summary} (replaced {occurrences} occurrence{'s' if occurrences != 1 else ''})"


# =============================================================================
# Write (full overwrite) — mirrors Claude Code's Write tool
# =============================================================================

@mcp.tool
async def write_resource(uri: str, content: str) -> str:
    """Overwrite a Sernia resource with new content.

    Use this for creating a new resource or restructuring an existing one
    end-to-end. For surgical edits to existing content, prefer
    ``edit_resource`` — it's token-efficient and atomic.

    Supported URIs:
      - ``memory://current`` — overwrite MEMORY.md
      - ``skill://<name>/SKILL.md`` — create or overwrite a skill

    The change is visible to the next ``sernia_context`` call and to the
    next session that initializes against this server.

    Args:
        uri: The resource URI to write.
        content: Full new content (replaces existing).
    """
    summary = _write_uri(uri, content)
    _schedule_git_sync()
    return summary
