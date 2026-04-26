"""Tests for the context layer: doorway tool, resources, edit_resource.

The conftest's ``_isolate_environment`` fixture points
``SERNIA_MCP_WORKSPACE_PATH`` at a per-test ``tmp_path``, so all filesystem
ops are scoped and parallel-safe.
"""
from __future__ import annotations

import json

import pytest
from fastmcp import Client

# ---------------------------------------------------------- core: list_skills

def test_list_skills_returns_empty_when_no_dir():
    from sernia_mcp.core.skills import list_skills

    assert list_skills() == []


def test_list_skills_parses_frontmatter_description(tmp_path):
    from sernia_mcp.core.skills import list_skills

    skill = tmp_path / "skills" / "communications" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\n"
        "description: How to talk to tenants\n"
        "---\n\n"
        "# Communications\n",
        encoding="utf-8",
    )

    skills = list_skills()
    assert len(skills) == 1
    s = skills[0]
    assert s.name == "communications"
    assert s.uri == "skill://communications/SKILL.md"
    assert s.description == "How to talk to tenants"


def test_list_skills_handles_missing_frontmatter(tmp_path):
    from sernia_mcp.core.skills import list_skills

    skill = tmp_path / "skills" / "minimal" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("Just a plain skill, no frontmatter.\n", encoding="utf-8")

    skills = list_skills()
    assert len(skills) == 1
    assert skills[0].description == ""


def test_list_skills_skips_dirs_without_skill_md(tmp_path):
    from sernia_mcp.core.skills import list_skills

    (tmp_path / "skills" / "valid").mkdir(parents=True)
    (tmp_path / "skills" / "valid" / "SKILL.md").write_text("ok", encoding="utf-8")
    (tmp_path / "skills" / "no_skill_md").mkdir(parents=True)

    names = {s.name for s in list_skills()}
    assert names == {"valid"}


# ---------------------------------------------------------- core: read/write

def test_read_skill_returns_full_content(tmp_path):
    from sernia_mcp.core.skills import read_skill

    skill = tmp_path / "skills" / "comms" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("the content", encoding="utf-8")

    assert read_skill("comms") == "the content"


def test_read_skill_rejects_path_traversal():
    from sernia_mcp.core.errors import ValidationError
    from sernia_mcp.core.skills import read_skill

    for bad in ("../etc", "..\\etc", "foo/bar", ".hidden", ""):
        with pytest.raises(ValidationError):
            read_skill(bad)


def test_read_skill_missing_raises_not_found():
    from sernia_mcp.core.errors import NotFoundError
    from sernia_mcp.core.skills import read_skill

    with pytest.raises(NotFoundError, match="skill not found"):
        read_skill("nonexistent")


def test_write_skill_creates_dir(tmp_path):
    from sernia_mcp.core.skills import read_skill, write_skill

    write_skill("brand_new", "fresh content")
    assert read_skill("brand_new") == "fresh content"
    assert (tmp_path / "skills" / "brand_new" / "SKILL.md").is_file()


def test_write_memory_and_read_memory_roundtrip(tmp_path):
    from sernia_mcp.core.skills import read_memory, write_memory

    assert read_memory() == ""  # no MEMORY.md yet
    write_memory("note to self")
    assert read_memory() == "note to self"
    assert (tmp_path / "MEMORY.md").is_file()


# ------------------------------------------------------- doorway tool over MCP

@pytest.fixture
async def mcp_client():
    from sernia_mcp.server import mcp

    async with Client(mcp) as c:
        yield c


@pytest.mark.asyncio
async def test_sernia_context_returns_memory_and_skill_list(tmp_path, mcp_client):
    from sernia_mcp.core.skills import write_memory, write_skill

    write_memory("the operating memory")
    write_skill("comms", "---\ndescription: How to message\n---\n\n# Comms\n")

    result = await mcp_client.call_tool("sernia_context", {})
    payload = json.loads(result.content[0].text)

    assert payload["memory"] == "the operating memory"
    assert len(payload["skills"]) == 1
    skill = payload["skills"][0]
    assert skill["name"] == "comms"
    assert skill["uri"] == "skill://comms/SKILL.md"
    assert skill["description"] == "How to message"
    assert "instructions" in payload


@pytest.mark.asyncio
async def test_sernia_context_works_with_no_memory_or_skills(mcp_client):
    """Empty workspace shouldn't crash — return empty memory + empty list."""
    result = await mcp_client.call_tool("sernia_context", {})
    payload = json.loads(result.content[0].text)
    assert payload["memory"] == ""
    assert payload["skills"] == []


# ---------------------------------------------------------------- resources

@pytest.mark.asyncio
async def test_memory_resource_listed_and_readable(tmp_path, mcp_client):
    from sernia_mcp.core.skills import write_memory

    write_memory("memory body")

    resources = await mcp_client.list_resources()
    assert any(str(r.uri) == "memory://current" for r in resources)

    contents = await mcp_client.read_resource("memory://current")
    assert contents[0].text == "memory body"


@pytest.mark.asyncio
async def test_skill_resource_template_readable(tmp_path, mcp_client):
    from sernia_mcp.core.skills import write_skill

    write_skill("rent", "rent skill body")

    contents = await mcp_client.read_resource("skill://rent/SKILL.md")
    assert contents[0].text == "rent skill body"


@pytest.mark.asyncio
async def test_skill_resource_template_listed(mcp_client):
    """The resource template URI should appear in resource templates list."""
    templates = await mcp_client.list_resource_templates()
    uris = {t.uriTemplate for t in templates}
    assert "skill://{name}/SKILL.md" in uris


# ---------------------------------------------------------- read_resource tool

@pytest.mark.asyncio
async def test_read_resource_returns_memory_content(tmp_path, mcp_client):
    from sernia_mcp.core.skills import write_memory

    write_memory("the operating memory body")

    result = await mcp_client.call_tool(
        "read_resource", {"uri": "memory://current"}
    )
    assert result.content[0].text == "the operating memory body"


@pytest.mark.asyncio
async def test_read_resource_returns_skill_content(tmp_path, mcp_client):
    from sernia_mcp.core.skills import write_skill

    write_skill("comms", "---\ndescription: how to message\n---\n\nBody.")

    result = await mcp_client.call_tool(
        "read_resource", {"uri": "skill://comms/SKILL.md"}
    )
    assert "Body." in result.content[0].text
    assert "description: how to message" in result.content[0].text


@pytest.mark.asyncio
async def test_read_resource_rejects_unknown_scheme(mcp_client):
    from fastmcp.exceptions import ToolError

    with pytest.raises(ToolError, match="unsupported URI"):
        await mcp_client.call_tool(
            "read_resource", {"uri": "file:///etc/passwd"}
        )


@pytest.mark.asyncio
async def test_read_resource_rejects_skill_uri_without_skill_md_suffix(mcp_client):
    from fastmcp.exceptions import ToolError

    with pytest.raises(ToolError, match=r"must end with /SKILL\.md"):
        await mcp_client.call_tool(
            "read_resource", {"uri": "skill://comms/notes.md"}
        )


@pytest.mark.asyncio
async def test_read_resource_missing_skill_raises_not_found(mcp_client):
    from fastmcp.exceptions import ToolError

    with pytest.raises(ToolError, match="skill not found"):
        await mcp_client.call_tool(
            "read_resource", {"uri": "skill://nonexistent/SKILL.md"}
        )


# --------------------------------------------------------- write_resource tool

@pytest.mark.asyncio
async def test_write_resource_updates_memory(tmp_path, mcp_client):
    result = await mcp_client.call_tool(
        "write_resource",
        {"uri": "memory://current", "content": "new memory"},
    )
    assert "wrote memory" in result.content[0].text

    from sernia_mcp.core.skills import read_memory

    assert read_memory() == "new memory"


@pytest.mark.asyncio
async def test_write_resource_creates_or_overwrites_skill(tmp_path, mcp_client):
    result = await mcp_client.call_tool(
        "write_resource",
        {"uri": "skill://invoicing/SKILL.md", "content": "new skill body"},
    )
    assert "wrote skill" in result.content[0].text
    assert "invoicing" in result.content[0].text

    from sernia_mcp.core.skills import read_skill

    assert read_skill("invoicing") == "new skill body"


@pytest.mark.asyncio
async def test_write_resource_rejects_unknown_scheme(mcp_client):
    from fastmcp.exceptions import ToolError

    with pytest.raises(ToolError, match="unsupported URI"):
        await mcp_client.call_tool(
            "write_resource",
            {"uri": "file:///etc/passwd", "content": "evil"},
        )


@pytest.mark.asyncio
async def test_write_resource_rejects_skill_uri_without_skill_md_suffix(mcp_client):
    from fastmcp.exceptions import ToolError

    with pytest.raises(ToolError, match=r"must end with /SKILL\.md"):
        await mcp_client.call_tool(
            "write_resource",
            {"uri": "skill://comms/notes.md", "content": "x"},
        )


@pytest.mark.asyncio
async def test_write_resource_rejects_path_traversal(mcp_client):
    from fastmcp.exceptions import ToolError

    with pytest.raises(ToolError, match="invalid skill name"):
        await mcp_client.call_tool(
            "write_resource",
            {"uri": "skill://..%2Fevil/SKILL.md", "content": "x"},
        )


# ------------------------------------ edit_resource tool (string substitution)

@pytest.mark.asyncio
async def test_edit_resource_replaces_unique_substring(tmp_path, mcp_client):
    from sernia_mcp.core.skills import read_memory, write_memory

    write_memory("Tenant: Anna lives in unit 02.")

    result = await mcp_client.call_tool(
        "edit_resource",
        {
            "uri": "memory://current",
            "old_string": "unit 02",
            "new_string": "unit 03",
        },
    )
    assert "replaced 1 occurrence" in result.content[0].text
    assert read_memory() == "Tenant: Anna lives in unit 03."


@pytest.mark.asyncio
async def test_edit_resource_fails_when_old_string_not_found(tmp_path, mcp_client):
    from fastmcp.exceptions import ToolError

    from sernia_mcp.core.skills import write_memory

    write_memory("hello world")

    with pytest.raises(ToolError, match="not found"):
        await mcp_client.call_tool(
            "edit_resource",
            {
                "uri": "memory://current",
                "old_string": "missing",
                "new_string": "anything",
            },
        )


@pytest.mark.asyncio
async def test_edit_resource_fails_on_ambiguous_old_string(tmp_path, mcp_client):
    """Two occurrences without ``replace_all`` must fail loudly — that's the
    Claude Code Edit safety property: one wrong replacement is worse than
    forcing the caller to disambiguate.
    """
    from fastmcp.exceptions import ToolError

    from sernia_mcp.core.skills import write_memory

    write_memory("foo\nfoo\n")

    with pytest.raises(ToolError, match="appears 2 times"):
        await mcp_client.call_tool(
            "edit_resource",
            {
                "uri": "memory://current",
                "old_string": "foo",
                "new_string": "bar",
            },
        )


@pytest.mark.asyncio
async def test_edit_resource_replace_all_flag(tmp_path, mcp_client):
    from sernia_mcp.core.skills import read_memory, write_memory

    write_memory("foo\nfoo\nfoo\n")

    result = await mcp_client.call_tool(
        "edit_resource",
        {
            "uri": "memory://current",
            "old_string": "foo",
            "new_string": "bar",
            "replace_all": True,
        },
    )
    assert "replaced 3 occurrences" in result.content[0].text
    assert read_memory() == "bar\nbar\nbar\n"


@pytest.mark.asyncio
async def test_edit_resource_no_op_rejected(tmp_path, mcp_client):
    """``old_string == new_string`` should fail rather than silently no-op."""
    from fastmcp.exceptions import ToolError

    from sernia_mcp.core.skills import write_memory

    write_memory("anything")

    with pytest.raises(ToolError, match="identical"):
        await mcp_client.call_tool(
            "edit_resource",
            {
                "uri": "memory://current",
                "old_string": "x",
                "new_string": "x",
            },
        )


@pytest.mark.asyncio
async def test_edit_resource_whitespace_exact(tmp_path, mcp_client):
    """Whitespace must match exactly — no fuzzy matching, like Claude Code."""
    from fastmcp.exceptions import ToolError

    from sernia_mcp.core.skills import write_memory

    write_memory("    indented line\n")

    # Tab-indented old_string should NOT match space-indented file content.
    with pytest.raises(ToolError, match="not found"):
        await mcp_client.call_tool(
            "edit_resource",
            {
                "uri": "memory://current",
                "old_string": "\tindented line",
                "new_string": "\tchanged",
            },
        )


@pytest.mark.asyncio
async def test_edit_resource_supports_skill_uris(tmp_path, mcp_client):
    from sernia_mcp.core.skills import read_skill, write_skill

    write_skill("rent", "Step 1: send invoice.\nStep 2: confirm.\n")

    await mcp_client.call_tool(
        "edit_resource",
        {
            "uri": "skill://rent/SKILL.md",
            "old_string": "send invoice",
            "new_string": "send detailed invoice",
        },
    )
    assert "send detailed invoice" in read_skill("rent")


@pytest.mark.asyncio
async def test_edit_resource_rejects_unknown_scheme(mcp_client):
    from fastmcp.exceptions import ToolError

    with pytest.raises(ToolError, match="unsupported URI"):
        await mcp_client.call_tool(
            "edit_resource",
            {
                "uri": "file:///etc/passwd",
                "old_string": "x",
                "new_string": "y",
            },
        )


# ----------------------------------------- write-then-read round-trip via MCP

@pytest.mark.asyncio
async def test_edit_then_sernia_context_reflects_change(tmp_path, mcp_client):
    """The full self-improving loop: write a skill, see it in the doorway
    tool's response, then read its content via the resource.
    """
    await mcp_client.call_tool(
        "write_resource",
        {
            "uri": "skill://reporting/SKILL.md",
            "content": (
                "---\n"
                "description: Generate monthly reports\n"
                "---\n\n"
                "# Reporting playbook\n"
            ),
        },
    )

    ctx = await mcp_client.call_tool("sernia_context", {})
    payload = json.loads(ctx.content[0].text)
    skill_uris = {s["uri"] for s in payload["skills"]}
    assert "skill://reporting/SKILL.md" in skill_uris

    contents = await mcp_client.read_resource("skill://reporting/SKILL.md")
    assert "Generate monthly reports" in contents[0].text
