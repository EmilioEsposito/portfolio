"""The agent's workspace file tools, driven through the real agent (no network).

Each test runs ``sernia_agent`` with a scripted model that calls the tools, so
it covers the whole path: the run's workspace from ``deps.workspace_path``,
the ``/workspace/`` adapter, the harness ``FileSystem`` tools, and
``ErrorLoggingToolset`` turning recoverable errors into text.
"""

from pathlib import Path

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from api.src.sernia_ai.deps import SerniaDeps
from api.src.sernia_ai.general_agent import sernia_agent
from api.src.sernia_ai.tools.workspace_tools import to_workspace_relative


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    (root / "areas").mkdir(parents=True)
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("[core]\n\tbare = false\n")
    (root / "MEMORY.md").write_text("# Memory\n- Unit 05 dishwasher: ordered\n")
    (root / "areas" / "units.md").write_text("Unit 05 is on the second floor.\n")
    (root / "areas" / "export.csv").write_text("a,b\n")
    return root


def _deps(root: Path) -> SerniaDeps:
    return SerniaDeps(
        db_session=None,  # type: ignore[arg-type] — no tool here touches the DB
        conversation_id="test-workspace-tools",
        user_identifier="user_test",
        user_name="Test User",
        user_email="test@serniacapital.com",
        modality="web_chat",
        workspace_path=root,
    )


async def _call(root: Path, *calls: tuple[str, dict]) -> list[str]:
    """Run the agent calling each tool in turn; return what each call returned."""
    steps = iter(calls)

    def model(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        step = next(steps, None)
        if step is None:
            return ModelResponse(parts=[TextPart("done")])
        return ModelResponse(parts=[ToolCallPart(*step)])

    with sernia_agent.override(native_tools=[]):
        result = await sernia_agent.run(
            "Work on the workspace.", deps=_deps(root), model=FunctionModel(model), output_type=str
        )
    assert result.output == "done"
    return [
        str(part.content)
        for message in result.all_messages()
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_name.startswith("workspace_")
    ]


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("/workspace/areas/units.md", "areas/units.md"),
        ("/workspace", "."),
        ("/workspace/", "."),
        ("areas/units.md", "areas/units.md"),
        ("/etc/passwd", "/etc/passwd"),
        ("/workspacex/a.md", "/workspacex/a.md"),
    ],
)
def test_workspace_paths_become_relative(given: str, expected: str):
    assert to_workspace_relative(given) == expected


@pytest.mark.asyncio
async def test_the_tools_the_model_sees(workspace: Path):
    seen: list[set[str]] = []

    def model(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append(
            {tool.name for tool in info.function_tools if tool.name.startswith("workspace_")}
        )
        return ModelResponse(parts=[TextPart("done")])

    with sernia_agent.override(native_tools=[]):
        await sernia_agent.run(
            "Hi.", deps=_deps(workspace), model=FunctionModel(model), output_type=str
        )

    assert seen[0] == {
        "workspace_read_file",
        "workspace_write_file",
        "workspace_edit_file",
        "workspace_list_directory",
        "workspace_search_files",
        "workspace_find_files",
        "workspace_delete_file",
    }


@pytest.mark.asyncio
async def test_read_with_and_without_the_workspace_prefix(workspace: Path):
    with_prefix, without = await _call(
        workspace,
        ("workspace_read_file", {"path": "/workspace/areas/units.md"}),
        ("workspace_read_file", {"path": "areas/units.md"}),
    )

    assert "Unit 05 is on the second floor." in with_prefix
    assert with_prefix == without


@pytest.mark.asyncio
async def test_write_creates_parent_directories(workspace: Path):
    (result,) = await _call(
        workspace,
        (
            "workspace_write_file",
            {
                "path": "/workspace/.claude/skills/move-out/SKILL.md",
                "content": "---\ndescription: d\n---\n",
            },
        ),
    )

    assert "Wrote" in result
    assert (
        (workspace / ".claude" / "skills" / "move-out" / "SKILL.md").read_text().startswith("---")
    )


@pytest.mark.asyncio
async def test_edit_changes_the_file(workspace: Path):
    (result,) = await _call(
        workspace,
        (
            "workspace_edit_file",
            {
                "path": "/workspace/MEMORY.md",
                "old_text": "- Unit 05 dishwasher: ordered",
                "new_text": "- Unit 05 dishwasher: delivered",
            },
        ),
    )

    assert "Edited" in result
    assert "delivered" in (workspace / "MEMORY.md").read_text()


@pytest.mark.asyncio
async def test_a_missed_edit_returns_the_re_read_hint_and_does_not_end_the_run(workspace: Path):
    """Five identical misses: a retry budget would have ended the run at the fourth."""
    miss = (
        "workspace_edit_file",
        {"path": "/workspace/MEMORY.md", "old_text": "  - Unit 05 dishwasher", "new_text": "x"},
    )
    results = await _call(workspace, *[miss] * 5)

    assert len(results) == 5
    assert all("old_text not found" in result for result in results)
    assert all("workspace_read_file" in result for result in results)
    assert "without the line numbers" in results[0]
    assert "STOP" not in results[0]
    assert "STOP" in results[1]
    assert (workspace / "MEMORY.md").read_text().startswith("# Memory")


@pytest.mark.asyncio
async def test_listing_shows_directories_and_finding_and_searching_work(workspace: Path):
    listed, found, searched = await _call(
        workspace,
        ("workspace_list_directory", {"path": "/workspace"}),
        ("workspace_find_files", {"pattern": "**/*.md"}),
        ("workspace_search_files", {"pattern": "second floor"}),
    )

    assert "areas/" in listed and "MEMORY.md" in listed
    assert "areas/units.md" in found and "MEMORY.md" in found
    assert "areas/units.md:1:" in searched


@pytest.mark.asyncio
async def test_delete_removes_a_file(workspace: Path):
    deleted, missing = await _call(
        workspace,
        ("workspace_delete_file", {"path": "/workspace/areas/units.md"}),
        ("workspace_delete_file", {"path": "/workspace/areas/units.md"}),
    )

    assert deleted == "Deleted areas/units.md."
    assert missing == "Path not found: areas/units.md"
    assert not (workspace / "areas" / "units.md").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "call",
    [
        ("workspace_read_file", {"path": "/workspace/areas/export.csv"}),
        ("workspace_write_file", {"path": "/workspace/script.py", "content": "x"}),
        ("workspace_delete_file", {"path": "/workspace/areas/export.csv"}),
    ],
    ids=["read-csv", "write-py", "delete-csv"],
)
async def test_other_file_types_are_refused(workspace: Path, call: tuple[str, dict]):
    (result,) = await _call(workspace, call)

    assert result.startswith(f"Error in {call[0]}:")
    assert "only .md, .txt, .json files" in result
    assert (workspace / "areas" / "export.csv").exists()
    assert not (workspace / "script.py").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "call",
    [
        ("workspace_read_file", {"path": "/etc/hosts.txt"}),
        ("workspace_read_file", {"path": "../outside.md"}),
        ("workspace_write_file", {"path": "../outside.md", "content": "x"}),
        ("workspace_delete_file", {"path": "../outside.md"}),
        ("workspace_read_file", {"path": "/workspace/.git/config.txt"}),
        ("workspace_write_file", {"path": "/workspace/.git/hooks.md", "content": "x"}),
        ("workspace_list_directory", {"path": "/workspace/.git"}),
    ],
    ids=["absolute", "read-up", "write-up", "delete-up", "read-git", "write-git", "list-git"],
)
async def test_nothing_outside_the_workspace_or_in_git_is_reachable(
    workspace: Path, call: tuple[str, dict]
):
    outside = workspace.parent / "outside.md"
    outside.write_text("keep")

    (result,) = await _call(workspace, call)

    assert result.startswith(f"Error in {call[0]}:")
    assert "config" not in result or "bare" not in result
    assert outside.read_text() == "keep"
    assert not (workspace / ".git" / "hooks.md").exists()


@pytest.mark.asyncio
async def test_search_and_find_only_show_workspace_file_types(workspace: Path):
    (workspace / "conf.yaml").write_text("api_key: not-for-the-model\nUnit 05 yaml\n")
    (workspace / ".env").write_text("TOKEN=not-for-the-model\n")
    searched, searched_hidden, found = await _call(
        workspace,
        ("workspace_search_files", {"pattern": "Unit 05"}),
        ("workspace_search_files", {"pattern": "TOKEN", "include_glob": ".env"}),
        ("workspace_find_files", {"pattern": "*"}),
    )

    assert "MEMORY.md" in searched and "areas/units.md" in searched
    assert "conf.yaml" not in searched and "not-for-the-model" not in searched
    assert "not-for-the-model" not in searched_hidden
    assert "conf.yaml" not in found and "export.csv" not in found


@pytest.mark.asyncio
async def test_a_link_cannot_reach_another_file_type(workspace: Path):
    (workspace / "conf.yaml").write_text("api_key: not-for-the-model\n")
    (workspace / "link.md").symlink_to(workspace / "conf.yaml")

    read, deleted = await _call(
        workspace,
        ("workspace_read_file", {"path": "link.md"}),
        ("workspace_delete_file", {"path": "link.md"}),
    )

    assert read.startswith("Error in workspace_read_file:")
    assert "not-for-the-model" not in read
    assert deleted.startswith("Error in workspace_delete_file:")
    assert (workspace / "conf.yaml").exists() and (workspace / "link.md").is_symlink()


@pytest.mark.asyncio
async def test_model_path_mistakes_come_back_as_warnings_without_host_paths(
    workspace: Path, monkeypatch
):
    """A bad path is the model's mistake: text it can act on, logged at warn, never paging."""
    from api.src.sernia_ai.tools import _logging

    levels: list[str] = []
    original = _logging.log_tool_error

    def record(tool_name, error, *, conversation_id="", level="error"):
        levels.append(level)
        return original(tool_name, error, conversation_id=conversation_id, level=level)

    monkeypatch.setattr(_logging, "log_tool_error", record)
    results = await _call(
        workspace,
        ("workspace_write_file", {"path": "MEMORY.md/inside.md", "content": "x"}),
        ("workspace_read_file", {"path": "/etc/hosts.md"}),
        ("workspace_edit_file", {"path": "missing.md", "old_text": "a", "new_text": "b"}),
        ("workspace_delete_file", {"path": "/workspace/.GIT/x.md"}),
    )

    assert levels == ["warn"] * len(results)
    assert all(str(workspace) not in result for result in results)
    # A missing file is not an edit miss: no "re-read the file" hint.
    assert "without the line numbers" not in results[2]


@pytest.mark.asyncio
async def test_a_conversation_continues_when_the_workspace_path_changes(tmp_path: Path):
    """Responses record the run's workspace; a resumed run checks it matches.

    Production's path is not development's, and a worktree's is not the main
    checkout's, so the recorded ref must not be the path.
    """
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    for root in (first_root, second_root):
        root.mkdir()
        (root / "MEMORY.md").write_text("# Memory\n")

    def model(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart("done")])

    with sernia_agent.override(native_tools=[]):
        first = await sernia_agent.run(
            "Hi.", deps=_deps(first_root), model=FunctionModel(model), output_type=str
        )
        second = await sernia_agent.run(
            "Again.",
            deps=_deps(second_root),
            model=FunctionModel(model),
            output_type=str,
            message_history=first.all_messages(),
        )

    assert second.output == "done"
