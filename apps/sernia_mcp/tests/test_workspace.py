"""Workspace read/write tests against a real (per-test) temp filesystem."""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_write_then_read_roundtrip(tmp_path: Path):
    from sernia_mcp.core.workspace.files import workspace_read_core, workspace_write_core

    write_result = await workspace_write_core("MEMORY.md", "hello world")
    assert write_result.created is True
    assert write_result.size_bytes == len(b"hello world")

    read_result = await workspace_read_core("MEMORY.md")
    assert read_result.content == "hello world"


@pytest.mark.asyncio
async def test_overwrite_sets_created_false():
    from sernia_mcp.core.workspace.files import workspace_write_core

    await workspace_write_core("notes.md", "v1")
    second = await workspace_write_core("notes.md", "v2")
    assert second.created is False


@pytest.mark.asyncio
async def test_path_escape_rejected():
    from sernia_mcp.core.errors import ValidationError
    from sernia_mcp.core.workspace.files import workspace_read_core

    with pytest.raises(ValidationError, match="escapes workspace root"):
        await workspace_read_core("../../etc/passwd")


@pytest.mark.asyncio
async def test_disallowed_suffix_rejected():
    from sernia_mcp.core.errors import ValidationError
    from sernia_mcp.core.workspace.files import workspace_write_core

    with pytest.raises(ValidationError, match="not allowed"):
        await workspace_write_core("evil.sh", "rm -rf /")


@pytest.mark.asyncio
async def test_workspace_prefix_stripped():
    from sernia_mcp.core.workspace.files import workspace_read_core, workspace_write_core

    await workspace_write_core("/workspace/areas/test.md", "abc")
    result = await workspace_read_core("/workspace/areas/test.md")
    assert result.content == "abc"


@pytest.mark.asyncio
async def test_missing_file_raises_not_found():
    from sernia_mcp.core.errors import NotFoundError
    from sernia_mcp.core.workspace.files import workspace_read_core

    with pytest.raises(NotFoundError, match="file not found"):
        await workspace_read_core("nope.md")
