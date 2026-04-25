"""Workspace file read/write — sandboxed under ``config.WORKSPACE_PATH``.

The workspace holds the cross-harness shared memory: MEMORY.md, skills,
daily notes, area-specific knowledge. Writes from MCP tools surface in the
same files the Sernia AI agent reads on its next run (when both services
point at the same path).

Path safety: rejects anything that escapes the workspace root and any suffix
not in ``_ALLOWED_SUFFIXES``.
"""
from __future__ import annotations

from pathlib import Path

from sernia_mcp.config import WORKSPACE_PATH
from sernia_mcp.core.errors import NotFoundError, ValidationError
from sernia_mcp.core.types import WorkspaceFile, WorkspaceWriteResult

_ALLOWED_SUFFIXES = frozenset({".md", ".txt", ".json"})


def _resolve_safe(path: str) -> Path:
    """Resolve user-supplied path to an absolute path inside ``WORKSPACE_PATH``."""
    cleaned = path.strip()
    if cleaned.startswith("/workspace/"):
        cleaned = cleaned[len("/workspace/"):]
    cleaned = cleaned.lstrip("/")
    if not cleaned:
        raise ValidationError("path is empty")

    root = WORKSPACE_PATH.resolve()
    candidate = (root / cleaned).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValidationError(f"path escapes workspace root: {path}") from exc

    if candidate.suffix not in _ALLOWED_SUFFIXES:
        raise ValidationError(
            f"suffix {candidate.suffix!r} not allowed; use one of {sorted(_ALLOWED_SUFFIXES)}"
        )
    return candidate


async def workspace_read_core(path: str) -> WorkspaceFile:
    """Read a file from the workspace."""
    resolved = _resolve_safe(path)
    if not resolved.is_file():
        raise NotFoundError(f"file not found: {path}")
    content = resolved.read_text(encoding="utf-8")
    return WorkspaceFile(
        path=f"/workspace/{resolved.relative_to(WORKSPACE_PATH.resolve())}",
        content=content,
        size_bytes=len(content.encode("utf-8")),
    )


async def workspace_write_core(path: str, content: str) -> WorkspaceWriteResult:
    """Write a file to the workspace, creating parent dirs."""
    resolved = _resolve_safe(path)
    created = not resolved.exists()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(content, encoding="utf-8")
    return WorkspaceWriteResult(
        path=f"/workspace/{resolved.relative_to(WORKSPACE_PATH.resolve())}",
        size_bytes=len(content.encode("utf-8")),
        created=created,
    )
