"""
Workspace file read/write core functions.

Operates on the same directory (`api/src/sernia_ai/workspace/`) that the
sernia_ai agent uses, so files written via MCP appear in MEMORY.md / skills /
areas on the next sernia_ai run — this is the cross-harness memory loop.

Paths are validated to prevent escape from the workspace root. Only text
suffixes (.md, .txt, .json) are allowed, matching the pydantic_ai sandbox
config in api/src/sernia_ai/agent.py.
"""
from pathlib import Path

from api.src.sernia_ai.config import WORKSPACE_PATH
from api.src.tool_core.errors import NotFoundError, ValidationError
from api.src.tool_core.types import WorkspaceFile, WorkspaceWriteResult

_ALLOWED_SUFFIXES = frozenset({".md", ".txt", ".json"})


def _resolve_safe(path: str) -> Path:
    """Resolve a user-supplied workspace path to an absolute path inside WORKSPACE_PATH.

    Accepts paths with or without a leading "/workspace/" prefix. Rejects any
    path that, after resolution, escapes WORKSPACE_PATH or uses a disallowed
    file suffix.
    """
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
    except ValueError:
        raise ValidationError(f"path escapes workspace root: {path}")

    if candidate.suffix not in _ALLOWED_SUFFIXES:
        raise ValidationError(
            f"suffix {candidate.suffix!r} not allowed; use one of {sorted(_ALLOWED_SUFFIXES)}"
        )
    return candidate


async def workspace_read_core(path: str) -> WorkspaceFile:
    """Read a file from the sernia_ai workspace.

    Args:
        path: Workspace-relative path, e.g. "MEMORY.md" or "/workspace/areas/properties.md".
    """
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
    """Write a file to the sernia_ai workspace, creating parent dirs if needed.

    Overwrites existing files. Returns whether the file was newly created.
    """
    resolved = _resolve_safe(path)
    created = not resolved.exists()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(content, encoding="utf-8")
    return WorkspaceWriteResult(
        path=f"/workspace/{resolved.relative_to(WORKSPACE_PATH.resolve())}",
        size_bytes=len(content.encode("utf-8")),
        created=created,
    )
