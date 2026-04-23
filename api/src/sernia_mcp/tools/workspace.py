"""MCP wrappers for workspace read/write."""
from fastmcp.exceptions import ToolError

from api.src.sernia_mcp.server import mcp
from api.src.tool_core.errors import CoreError, NotFoundError, ValidationError
from api.src.tool_core.types import WorkspaceFile, WorkspaceWriteResult
from api.src.tool_core.workspace.files import workspace_read_core, workspace_write_core


@mcp.tool
async def workspace_read(path: str) -> WorkspaceFile:
    """Read a file from the Sernia workspace.

    The workspace contains the shared long-term memory (MEMORY.md), skills
    (skills/<name>/SKILL.md), daily notes, and area-specific knowledge. Files
    written here are also visible to the Sernia AI agent on its next run.

    Args:
        path: Workspace-relative path, e.g. "MEMORY.md" or "areas/properties.md".
              A leading "/workspace/" is accepted and stripped.
    """
    try:
        return await workspace_read_core(path)
    except NotFoundError as e:
        raise ToolError(str(e)) from e
    except ValidationError as e:
        raise ToolError(f"invalid path: {e}") from e
    except CoreError as e:
        raise ToolError(f"workspace_read failed: {e}") from e


@mcp.tool
async def workspace_write(path: str, content: str) -> WorkspaceWriteResult:
    """Write a file to the Sernia workspace (creates parent directories).

    Overwrites existing files. Use this to update MEMORY.md, create daily
    notes, or refine skills. Changes are visible to the Sernia AI agent on
    its next run.

    Only .md, .txt, and .json files are allowed.

    Args:
        path: Workspace-relative path.
        content: Full file contents (UTF-8). Existing file is overwritten.
    """
    try:
        return await workspace_write_core(path, content)
    except ValidationError as e:
        raise ToolError(f"invalid path: {e}") from e
    except CoreError as e:
        raise ToolError(f"workspace_write failed: {e}") from e
