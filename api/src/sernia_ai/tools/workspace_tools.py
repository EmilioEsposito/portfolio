"""Workspace file tools: the harness ``FileSystem`` tools, adapted to Sernia's workspace.

The agent's memory is a git-backed directory (``WORKSPACE_PATH``, a clone of
the sernia-knowledge repo). These tools read and write it. They are the
harness ``FileSystem`` toolset (path containment, symlink checks, bounded
reads and searches) with an adapter that keeps the contract the agent's
prompts, skills and stored conversations were written against:

- **Paths.** The model writes ``/workspace/areas/x.md``; the harness only
  takes paths relative to the workspace root. ``/workspace/`` is stripped, so
  either form works. Paths in tool output are relative.
- **File types.** Only ``.md``, ``.txt`` and ``.json`` files can be read,
  written, edited or deleted. This is enforced here, not with the harness's
  ``allowed_patterns``, which would also hide directories from listings.
  ``.git`` is denied outright, so git config is never readable.
- **Parent directories** are created on write, as the previous tools did.
- **Delete.** The harness has no delete tool; ``delete_file`` is added here.
- **Errors.** The harness reports a bad path or an edit whose ``old_text`` is
  missing as a tool retry. Retries count against the run's budget, and a model
  stuck re-sending the same edit would end the run. They are re-raised as
  ``WorkspaceToolError`` instead, which ``ErrorLoggingToolset`` returns to the
  model as text, logs at warning level, and escalates with "STOP" guidance on
  identical repeats (see ``_logging.py``).

``SerniaWorkspace`` contributes the tools and sets each run's workspace to its
``deps.workspace_path``.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.toolsets import AbstractToolset, CombinedToolset, FunctionToolset, WrapperToolset
from pydantic_ai.workspaces import LocalWorkspaceBackend, WorkspaceBackend, WorkspaceRef
from pydantic_ai_harness.filesystem import FileSystem

from api.src.sernia_ai.deps import SerniaDeps

CAPABILITY_ID = "sernia-workspace"
"""The capability's id. Shares the skill id space, so ``agent.py`` reserves it."""

WORKSPACE_FILE_SUFFIXES: tuple[str, ...] = (".md", ".txt", ".json")
"""The only file types the tools read or write."""

_VIRTUAL_ROOT = "/workspace"
"""How the agent's prompts and skills name the workspace root."""

_WORKSPACE_REF = WorkspaceRef(provider="local", id="sernia-workspace")
"""The ref recorded on responses: the same in every environment. See ``_SerniaWorkspaceBackend``."""

# Files the tools never read, list or search, whatever their type: git's own
# data, and anything that looks like a secret. Matched without regard to case.
_DENIED_PATTERNS = [".git", "**/.git", ".env", "**/.env", ".env.*", "**/.env.*", "*.pem", "*.key"]

# Tools that act on one file, so its type is checked first.
_FILE_TOOLS = frozenset({"read_file", "write_file", "edit_file", "delete_file"})

# The harness tools offered. `create_directory` is left out because writes
# create parents, and `file_info` because nothing the agent does needs it.
_HARNESS_TOOLS = (
    "read_file",
    "write_file",
    "edit_file",
    "list_directory",
    "search_files",
    "find_files",
)


class WorkspaceToolError(Exception):
    """A workspace tool call the model can fix by changing its arguments."""


class WorkspaceEditMiss(WorkspaceToolError):
    """An edit whose ``old_text`` is not in the file."""


def to_workspace_relative(path: str) -> str:
    """``/workspace/areas/x.md`` -> ``areas/x.md``; ``/workspace`` -> ``.``.

    Other paths are returned unchanged, so the harness's own containment
    check decides on them (an absolute path elsewhere is refused).
    """
    if path == _VIRTUAL_ROOT or path == _VIRTUAL_ROOT + "/":
        return "."
    if path.startswith(_VIRTUAL_ROOT + "/"):
        return path[len(_VIRTUAL_ROOT) + 1 :] or "."
    return path


def _is_workspace_file(name: str) -> bool:
    return PurePosixPath(name).suffix.lower() in WORKSPACE_FILE_SUFFIXES


def _check_file_type(path: str, target: Path) -> None:
    """Refuse a path, or a symlink's target, that is not a workspace file type."""
    if not (_is_workspace_file(path) and _is_workspace_file(target.name)):
        allowed = ", ".join(WORKSPACE_FILE_SUFFIXES)
        raise WorkspaceToolError(f"Path {path!r} is not a workspace file; only {allowed} files.")


def _inside(root: Path, relative: str) -> Path:
    """The host path for ``relative``, symlinks resolved.

    Refuses anything outside ``root`` or in ``.git``. The error names the path
    as the model wrote it, never the host path.
    """
    target = (root / relative).resolve()
    resolved_root = root.resolve()
    if target != resolved_root and resolved_root not in target.parents:
        raise WorkspaceToolError(f"Path {relative!r} is outside the workspace.")
    if any(part.lower() == ".git" for part in target.relative_to(resolved_root).parts):
        raise WorkspaceToolError(f"Path {relative!r} is inside .git, which the tools cannot touch.")
    return target


def _keep_workspace_files(output: str) -> str:
    """Drop result lines for files of other types from a search or find.

    The harness searches every file it may read; the tools only read
    ``.md``, ``.txt`` and ``.json``, so results from other files are removed.
    Lines that are not file results (notes, "No matches found.") are kept.
    """
    kept: list[str] = []
    dropped = False
    for line in output.splitlines():
        if line.startswith("[") or line.startswith("No matches"):
            kept.append(line)
            continue
        name = line.split(":", 1)[0] if ":" in line else line
        if name.endswith("/") or _is_workspace_file(name):
            kept.append(line)
        else:
            dropped = True
    if dropped and not any(_looks_like_result(line) for line in kept):
        return "No matches found."
    return "\n".join(kept)


def _looks_like_result(line: str) -> bool:
    return bool(line) and not line.startswith("[") and not line.startswith("No matches")


_delete_toolset = FunctionToolset[SerniaDeps]()


@_delete_toolset.tool
async def delete_file(ctx: RunContext[SerniaDeps], path: str) -> str:
    """Delete one file from the workspace.

    Args:
        path: The file to delete, relative to the workspace root (`/workspace/...` also works).
    """
    root = ctx.deps.workspace_path
    target = _inside(root, path)
    if (root / path).is_symlink():
        raise WorkspaceToolError(f"Path {path!r} is a link; the tools do not delete links.")
    _check_file_type(path, target)
    if not target.exists():
        return f"Path not found: {path}"
    if not target.is_file():
        raise WorkspaceToolError(f"Path {path!r} is not a file.")
    try:
        target.unlink()
    except OSError as e:
        raise WorkspaceToolError(f"Could not delete {path!r}: {e.strerror or e}.") from e
    return f"Deleted {path}."


class WorkspaceFileToolset(WrapperToolset[SerniaDeps]):
    """The adapter described in this module's docstring."""

    async def get_tools(self, ctx: RunContext[SerniaDeps]) -> dict[str, Any]:
        if _tools_disabled.get():
            return {}
        return await super().get_tools(ctx)

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[SerniaDeps],
        tool: Any,
    ) -> Any:
        if isinstance(tool_args.get("path"), str):
            tool_args = {**tool_args, "path": to_workspace_relative(tool_args["path"])}
        path = tool_args.get("path")
        if isinstance(path, str) and name != "delete_file":
            # Checked here as well as by the harness, so a bad path is the
            # model's error, worded with its own path, never the host's.
            target = _inside(ctx.deps.workspace_path, path)
            if name in _FILE_TOOLS:
                _check_file_type(path, target)
            if name == "write_file":
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                except OSError as e:
                    raise WorkspaceToolError(
                        f"Could not create the folder for {path!r}: {e.strerror or e}."
                    ) from e
        try:
            result = await super().call_tool(name, tool_args, ctx, tool)
        except ModelRetry as e:
            message = str(e)
            if name == "edit_file" and message.startswith("old_text not found"):
                raise WorkspaceEditMiss(message) from e
            raise WorkspaceToolError(message) from e
        if name in ("search_files", "find_files") and isinstance(result, str):
            return _keep_workspace_files(result)
        return result


class _SerniaWorkspaceBackend(LocalWorkspaceBackend):
    """A local workspace whose ref does not name its path.

    pydantic-ai records the run workspace's ref on every response, and a run
    that continues a conversation refuses a workspace whose ref differs. The
    local backend's ref is its absolute path, which is not the same in every
    environment or checkout (a conversation from production replayed in
    development, a volume mounted elsewhere), so the ref here is a fixed name.
    """

    @property
    def ref(self) -> WorkspaceRef:
        return _WORKSPACE_REF


# Set by the A/B harness for runs that must not touch the workspace.
_tools_disabled: ContextVar[bool] = ContextVar("workspace_tools_disabled", default=False)


@contextmanager
def workspace_tools_disabled() -> Iterator[None]:
    """Offer no workspace file tools to runs started inside this block."""
    token = _tools_disabled.set(True)
    try:
        yield
    finally:
        _tools_disabled.reset(token)


class SerniaWorkspace(AbstractCapability[SerniaDeps]):
    """The workspace file tools, and each run's ``deps.workspace_path`` as its workspace.

    The harness file tools only work when a capability contributes them, so
    they come from here rather than the agent's ``toolsets``.
    """

    def __init__(self, toolset: AbstractToolset[SerniaDeps]) -> None:
        self.id = CAPABILITY_ID
        self._toolset = toolset

    def get_workspace(
        self, ctx: RunContext[SerniaDeps], *, ref: Any = None
    ) -> WorkspaceBackend | None:
        if ctx.deps is None:
            return None
        return _SerniaWorkspaceBackend(ctx.deps.workspace_path)

    def get_toolset(self) -> AbstractToolset[SerniaDeps]:
        return self._toolset


def build_workspace_toolset() -> WorkspaceFileToolset:
    file_system = FileSystem[SerniaDeps](
        # `.git` holds the repo's config, including its remote.
        denied_patterns=_DENIED_PATTERNS,
        # Hashes protect against a stale write, but cost tokens on every read
        # and write and need the model to pass them back; edits here are short
        # and re-read on failure instead.
        content_hashes=False,
        tools=list(_HARNESS_TOOLS),
    )
    return WorkspaceFileToolset(CombinedToolset([file_system.get_toolset(), _delete_toolset]))
