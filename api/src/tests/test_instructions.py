"""Unit tests for sernia_ai.instructions filetree rendering + refresh_from_remote."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.src.sernia_ai.instructions import (
    _build_filetree,
    _COLLAPSED_PATHS,
    _pulled_conversation_ids,
    refresh_from_remote,
)


def test_filetree_collapses_daily_notes(tmp_path: Path):
    """daily_notes/ should render as a count, not expand each file."""
    assert "daily_notes" in _COLLAPSED_PATHS

    (tmp_path / "MEMORY.md").write_text("memory")
    (tmp_path / "areas").mkdir()
    (tmp_path / "areas" / "tenants.md").write_text("tenants")

    daily = tmp_path / "daily_notes"
    daily.mkdir()
    for i in range(15):
        (daily / f"2026-04-{i:02d}_note.md").write_text("note")

    tree = _build_filetree(tmp_path)
    assert "daily_notes/ (15 entries)" in tree
    # Individual daily notes should NOT appear
    assert "2026-04-00_note.md" not in tree
    # areas/ stays expanded
    assert "areas" in tree
    assert "tenants.md" in tree


def test_filetree_shows_gitkeep_only_dirs(tmp_path: Path):
    """Empty / placeholder-only directories should still render — areas/ is
    the canonical example: even when empty, it's a known path the agent uses."""
    (tmp_path / "areas").mkdir()
    (tmp_path / "areas" / ".gitkeep").write_text("# placeholder")
    (tmp_path / "MEMORY.md").write_text("memory")

    tree = _build_filetree(tmp_path)
    assert "MEMORY.md" in tree
    assert "areas" in tree


def test_filetree_hides_mcp_json(tmp_path: Path):
    """.mcp.json is for human / Claude-CLI tooling and should not appear."""
    (tmp_path / ".mcp.json").write_text("{}")
    (tmp_path / "MEMORY.md").write_text("m")

    tree = _build_filetree(tmp_path)
    assert "MEMORY.md" in tree
    assert ".mcp.json" not in tree


def test_filetree_collapses_only_claude_skills_subtree(tmp_path: Path):
    """`.claude/skills` collapses (skills are reached via list_skills /
    load_skill); `.claude` itself stays navigable for other tooling files."""
    skills = tmp_path / ".claude" / "skills" / "communications"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text("---\ndescription: x\n---\n")
    (tmp_path / ".claude" / "settings.json").write_text("{}")
    (tmp_path / "MEMORY.md").write_text("m")

    tree = _build_filetree(tmp_path)
    assert ".claude" in tree
    # The subtree is collapsed — no skill names / SKILL.md leak
    assert "skills/ (1 entries)" in tree
    assert "communications" not in tree
    assert "SKILL.md" not in tree
    # Sibling files inside .claude are still visible
    assert "settings.json" in tree


def test_filetree_only_collapses_at_top_level(tmp_path: Path):
    """A nested directory named daily_notes should still expand normally."""
    nested = tmp_path / "areas" / "daily_notes"
    nested.mkdir(parents=True)
    (nested / "x.md").write_text("x")

    tree = _build_filetree(tmp_path)
    assert "x.md" in tree
    assert "(1 entries)" not in tree


# =====================================================================
# refresh_from_remote — pull only on first turn of each conversation
# =====================================================================

@pytest.fixture(autouse=True)
def _clear_pulled_conversation_cache():
    """The pulled-conversations set is process-global; clear before each test."""
    _pulled_conversation_ids.clear()
    yield
    _pulled_conversation_ids.clear()


def _ctx(conversation_id: str, workspace_path: Path) -> SimpleNamespace:
    """Minimal RunContext stand-in. ``refresh_from_remote`` only reads
    ``ctx.deps.conversation_id`` and ``ctx.deps.workspace_path``.
    """
    return SimpleNamespace(
        deps=SimpleNamespace(
            conversation_id=conversation_id,
            workspace_path=workspace_path,
        ),
    )


@pytest.mark.asyncio
async def test_refresh_pulls_on_first_turn(tmp_path):
    """First call for a conversation_id triggers pull_workspace."""
    with patch(
        "api.src.sernia_ai.memory.git_sync.pull_workspace", new=AsyncMock()
    ) as fake_pull:
        result = await refresh_from_remote(_ctx("conv-A", tmp_path))

    assert result == ""
    fake_pull.assert_awaited_once_with(tmp_path)


@pytest.mark.asyncio
async def test_refresh_skips_pull_on_followup_turn(tmp_path):
    """Subsequent turns in the same conversation must NOT pull — that's
    the whole point of the optimization. Avoids ~300-500ms latency on
    every user message after the first.
    """
    with patch(
        "api.src.sernia_ai.memory.git_sync.pull_workspace", new=AsyncMock()
    ) as fake_pull:
        await refresh_from_remote(_ctx("conv-A", tmp_path))
        await refresh_from_remote(_ctx("conv-A", tmp_path))
        await refresh_from_remote(_ctx("conv-A", tmp_path))

    fake_pull.assert_awaited_once()  # only the first call


@pytest.mark.asyncio
async def test_refresh_pulls_per_conversation(tmp_path):
    """Different conversations each get one pull on their first turn."""
    with patch(
        "api.src.sernia_ai.memory.git_sync.pull_workspace", new=AsyncMock()
    ) as fake_pull:
        await refresh_from_remote(_ctx("conv-A", tmp_path))
        await refresh_from_remote(_ctx("conv-B", tmp_path))
        await refresh_from_remote(_ctx("conv-A", tmp_path))  # follow-up
        await refresh_from_remote(_ctx("conv-C", tmp_path))

    # A, B, C → 3 distinct first-turn pulls; A's follow-up is suppressed.
    assert fake_pull.await_count == 3


@pytest.mark.asyncio
async def test_refresh_marks_conversation_even_on_pull_failure(tmp_path):
    """If pull_workspace raises, we still mark the conversation as pulled
    so we don't retry on every follow-up turn (which would compound the
    latency for a known-broken sync).
    """
    with patch(
        "api.src.sernia_ai.memory.git_sync.pull_workspace",
        new=AsyncMock(side_effect=RuntimeError("simulated git outage")),
    ) as fake_pull:
        # Must not raise (fail-soft contract).
        await refresh_from_remote(_ctx("conv-A", tmp_path))
        await refresh_from_remote(_ctx("conv-A", tmp_path))

    # Pull only attempted once despite the failure.
    fake_pull.assert_awaited_once()
    assert "conv-A" in _pulled_conversation_ids
