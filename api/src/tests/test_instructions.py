"""Unit tests for sernia_ai.instructions filetree rendering."""

from pathlib import Path

from api.src.sernia_ai.instructions import _build_filetree, _COLLAPSED_DIRS


def test_filetree_collapses_daily_notes(tmp_path: Path):
    """daily_notes/ should render as a count, not expand each file."""
    assert "daily_notes" in _COLLAPSED_DIRS

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


def test_filetree_handles_empty_daily_notes(tmp_path: Path):
    (tmp_path / "daily_notes").mkdir()
    tree = _build_filetree(tmp_path)
    assert "daily_notes/ (0 entries)" in tree


def test_filetree_only_collapses_at_top_level(tmp_path: Path):
    """A nested directory named daily_notes should still expand normally."""
    nested = tmp_path / "areas" / "daily_notes"
    nested.mkdir(parents=True)
    (nested / "x.md").write_text("x")

    tree = _build_filetree(tmp_path)
    assert "x.md" in tree
    assert "(1 entries)" not in tree
