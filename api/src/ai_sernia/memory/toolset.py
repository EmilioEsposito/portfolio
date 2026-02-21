"""
Memory file tools for the Sernia Capital AI agent.

Provides sandboxed read/write access to the .workspace/ directory.
All paths are relative to workspace_path; traversal is blocked.
"""
from pydantic_ai import RunContext
from pydantic_ai.toolsets import FunctionToolset

from api.src.ai_sernia.deps import SerniaDeps
from api.src.ai_sernia.memory import resolve_safe_path

memory_toolset = FunctionToolset()


@memory_toolset.tool
async def read_file(ctx: RunContext[SerniaDeps], path: str) -> str:
    """
    Read a file from the workspace.

    Args:
        path: Relative path within the workspace (e.g. "MEMORY.md", "daily_notes/2025-01-15.md")
    """
    resolved = resolve_safe_path(ctx.deps.workspace_path, path)

    if not resolved.exists():
        return f"File not found: {path}"
    if not resolved.is_file():
        return f"Not a file: {path}"

    content = resolved.read_text(encoding="utf-8")
    # Cap at 10k chars to avoid blowing up context
    if len(content) > 10_000:
        return content[:10_000] + f"\n\n... (truncated, {len(content)} total chars)"
    return content


@memory_toolset.tool
async def write_file(ctx: RunContext[SerniaDeps], path: str, content: str) -> str:
    """
    Create or overwrite a file in the workspace.

    Args:
        path: Relative path within the workspace (e.g. "MEMORY.md", "areas/properties.md")
        content: Full file content to write
    """
    resolved = resolve_safe_path(ctx.deps.workspace_path, path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(content, encoding="utf-8")
    return f"Written {len(content)} chars to {path}"


@memory_toolset.tool
async def append_to_file(ctx: RunContext[SerniaDeps], path: str, content: str) -> str:
    """
    Append content to an existing file, or create it if missing.
    Useful for daily notes and running logs.

    Args:
        path: Relative path within the workspace (e.g. "daily_notes/2025-01-15.md")
        content: Content to append (a newline is prepended if the file already has content)
    """
    resolved = resolve_safe_path(ctx.deps.workspace_path, path)
    resolved.parent.mkdir(parents=True, exist_ok=True)

    if resolved.exists():
        existing = resolved.read_text(encoding="utf-8")
        separator = "\n" if existing and not existing.endswith("\n") else ""
        resolved.write_text(existing + separator + content, encoding="utf-8")
        return f"Appended {len(content)} chars to {path}"
    else:
        resolved.write_text(content, encoding="utf-8")
        return f"Created {path} with {len(content)} chars"


@memory_toolset.tool
async def list_directory(ctx: RunContext[SerniaDeps], path: str = "") -> str:
    """
    List files and directories in the workspace.

    Args:
        path: Relative directory path (empty string for workspace root)
    """
    workspace = ctx.deps.workspace_path.resolve()

    if path.strip():
        target = resolve_safe_path(workspace, path)
    else:
        target = workspace

    if not target.exists():
        return f"Directory not found: {path or '.'}"
    if not target.is_dir():
        return f"Not a directory: {path}"

    entries = sorted(target.iterdir())
    if not entries:
        return f"Empty directory: {path or '.'}"

    lines = []
    for entry in entries:
        rel = entry.relative_to(workspace)
        if entry.is_dir():
            lines.append(f"  {rel}/")
        else:
            size = entry.stat().st_size
            lines.append(f"  {rel} ({size} bytes)")

    return "\n".join(lines)
