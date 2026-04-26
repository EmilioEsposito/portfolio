"""Skill discovery + frontmatter parsing.

Skills live at ``<WORKSPACE_PATH>/skills/<name>/SKILL.md``. Each one is a
markdown file with optional YAML frontmatter (the same ``description:`` /
``---`` convention used by ``.claude/skills/``).

This module just walks the directory and parses the minimum metadata needed
for the doorway tool's skill listing — full content goes through the
``skill://{name}/SKILL.md`` resource template, not through here.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sernia_mcp.config import WORKSPACE_PATH
from sernia_mcp.core.errors import NotFoundError, ValidationError


@dataclass(frozen=True)
class SkillMeta:
    """Lightweight metadata about a skill — name, URI, description (from frontmatter)."""

    name: str
    uri: str
    description: str


def _parse_frontmatter_description(text: str) -> str:
    """Extract the `description:` value from YAML frontmatter at the top of a
    markdown file. Returns "" if there's no frontmatter or no description.

    We don't pull in PyYAML — the format is simple enough to parse by hand,
    and the alternative is forcing a transitive dependency for one field.
    """
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    if end == -1:
        return ""
    front = text[3:end]
    for raw_line in front.splitlines():
        line = raw_line.strip()
        if line.startswith("description:"):
            return line[len("description:"):].strip().strip("\"'")
    return ""


def list_skills() -> list[SkillMeta]:
    """Return metadata for every skill under ``<WORKSPACE_PATH>/skills/``.

    Each subdirectory containing a readable ``SKILL.md`` becomes one entry.
    Sorted by name for stable output.
    """
    skills_dir = WORKSPACE_PATH / "skills"
    if not skills_dir.is_dir():
        return []

    skills: list[SkillMeta] = []
    for entry in sorted(skills_dir.iterdir()):
        skill_md = entry / "SKILL.md"
        if not skill_md.is_file():
            continue
        try:
            text = skill_md.read_text(encoding="utf-8")
        except OSError:
            continue
        skills.append(
            SkillMeta(
                name=entry.name,
                uri=f"skill://{entry.name}/SKILL.md",
                description=_parse_frontmatter_description(text),
            )
        )
    return skills


def _validate_skill_name(name: str) -> None:
    """Reject anything that isn't a plain skill name."""
    if not name or "/" in name or "\\" in name or ".." in name or name.startswith("."):
        raise ValidationError(f"invalid skill name: {name!r}")


def read_skill(name: str) -> str:
    """Read full ``SKILL.md`` content for a named skill."""
    _validate_skill_name(name)
    skill_md = WORKSPACE_PATH / "skills" / name / "SKILL.md"
    if not skill_md.is_file():
        raise NotFoundError(f"skill not found: {name}")
    return skill_md.read_text(encoding="utf-8")


def write_skill(name: str, content: str) -> Path:
    """Overwrite ``SKILL.md`` for a skill, creating the directory if needed."""
    _validate_skill_name(name)
    skill_dir = WORKSPACE_PATH / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(content, encoding="utf-8")
    return skill_md


def read_memory() -> str:
    """Read ``MEMORY.md`` content, or empty string if it doesn't exist yet."""
    memory_md = WORKSPACE_PATH / "MEMORY.md"
    if not memory_md.is_file():
        return ""
    return memory_md.read_text(encoding="utf-8")


def write_memory(content: str) -> Path:
    """Overwrite ``MEMORY.md``, creating ``WORKSPACE_PATH`` if needed."""
    WORKSPACE_PATH.mkdir(parents=True, exist_ok=True)
    memory_md = WORKSPACE_PATH / "MEMORY.md"
    memory_md.write_text(content, encoding="utf-8")
    return memory_md
