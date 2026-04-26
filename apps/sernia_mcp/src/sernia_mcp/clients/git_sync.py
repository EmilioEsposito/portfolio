# ruff: noqa: RUF059
"""Git-backed sync for the Sernia knowledge workspace.

Vendored from ``api/src/sernia_ai/memory/git_sync.py`` so that the MCP service
and the existing Sernia AI agent can write to the **same** GitHub repo
(``EmilioEsposito/sernia-knowledge``). When this service edits MEMORY.md or a
skill via ``edit_resource``, the change is committed and pushed; the next
sernia_ai agent run pulls the same change. Same on the way back: edits made
through the agent flow into the MCP workspace on the next pull.

Backs the configured workspace directory with a private GitHub repo so:
  - File edits are visible as git commits on GitHub
  - Knowledge can be edited directly on GitHub
  - Version history comes for free
  - Both this MCP service and the sernia_ai agent share state through git

Requires ``GITHUB_EMILIO_PERSONAL_WRITE_PAT`` env var. Without it, the workspace
operates in local-only mode (no git sync) — useful for tests and dev. When
the api/ copy of this file changes meaningfully, mirror the change here. The
contract is small enough that occasional drift is acceptable; document any
intentional divergence in CLAUDE.md.

The ``RUF059`` ruff rule (unused unpacked variables) is suppressed file-wide
to match the upstream copy in ``api/src/sernia_ai/memory/`` — we keep this
file as close to upstream as possible to make periodic sync trivial.
"""

import asyncio
import os
from pathlib import Path

import logfire

REPO = "EmilioEsposito/sernia-knowledge"
_push_lock = asyncio.Lock()


def _get_pat() -> str | None:
    return os.environ.get("GITHUB_EMILIO_PERSONAL_WRITE_PAT")


def _remote_url(pat: str) -> str:
    return f"https://{pat}@github.com/{REPO}.git"


def _redact(text: str, pat: str) -> str:
    """Redact PAT from log output."""
    return text.replace(pat, "***PAT***")


async def _run_git(
    *args: str,
    cwd: Path,
    pat: str | None = None,
) -> tuple[int, str, str]:
    """Run a git command asynchronously. Returns (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, stderr_bytes = await proc.communicate()
    stdout = stdout_bytes.decode(errors="replace")
    stderr = stderr_bytes.decode(errors="replace")

    if pat:
        stdout = _redact(stdout, pat)
        stderr = _redact(stderr, pat)

    return proc.returncode, stdout, stderr


async def _configure_repo(workspace: Path, pat: str) -> None:
    """Set repo-local git config for the MCP service's commits."""
    await _run_git("config", "user.name", "Sernia MCP", cwd=workspace)
    await _run_git("config", "user.email", "ai-agent@serniacapital.com", cwd=workspace)
    rc, _, _ = await _run_git("remote", "get-url", "origin", cwd=workspace, pat=pat)
    if rc == 0:
        await _run_git("remote", "set-url", "origin", _remote_url(pat), cwd=workspace, pat=pat)
    else:
        await _run_git("remote", "add", "origin", _remote_url(pat), cwd=workspace, pat=pat)


async def _commit_and_push_dirty(workspace: Path, pat: str) -> None:
    """Commit and push any uncommitted workspace changes. Called once at startup."""
    rc, stdout, _ = await _run_git("status", "--porcelain", cwd=workspace)
    if rc != 0 or not stdout.strip():
        return

    logfire.info("git_sync: found uncommitted changes on startup, committing")
    await _run_git("add", "-A", cwd=workspace)
    rc, _, _ = await _run_git("diff", "--cached", "--quiet", cwd=workspace)
    if rc == 0:
        return
    await _run_git("commit", "-m", "mcp: commit uncommitted changes from previous run", cwd=workspace)
    rc, _, stderr = await _run_git("push", "-u", "origin", "main", cwd=workspace, pat=pat)
    if rc != 0:
        logfire.error(f"git_sync: startup push failed: {stderr}")
    else:
        logfire.info("git_sync: startup push succeeded")


async def ensure_repo(workspace_path: Path) -> None:
    """Ensure ``workspace_path`` is backed by the git repo. Called once at startup.

    Scenarios:
      - PAT not set → no-op (local-only fallback, safe for tests + dev)
      - Not a git repo, dir empty → ``git clone``
      - Not a git repo, dir has files → ``git init`` + add remote + fetch
      - Already a git repo → ``git pull`` (non-fatal on failure)
    """
    pat = _get_pat()
    if not pat:
        logfire.info("git_sync: No PAT set, workspace will be local-only")
        return

    workspace_path.mkdir(parents=True, exist_ok=True)
    git_dir = workspace_path / ".git"
    is_git_repo = git_dir.exists()

    if is_git_repo:
        logfire.info("git_sync: Existing repo, pulling latest")
        await _configure_repo(workspace_path, pat)

        rc_status, status_out, _ = await _run_git("status", "--porcelain", cwd=workspace_path)
        if "U " in (status_out or "") or " U" in (status_out or ""):
            logfire.warn("git_sync: found unmerged files on startup, committing as-is to unblock")
            await _run_git("add", "-A", cwd=workspace_path)
            await _run_git("commit", "-m", "mcp: commit unmerged files on startup", cwd=workspace_path)

        rc, stdout, stderr = await _run_git(
            "pull", "--rebase=false", "origin", "main",
            cwd=workspace_path, pat=pat,
        )
        if rc != 0 and "unrelated histories" in stderr:
            logfire.warn("git_sync: unrelated histories on startup, retrying with --allow-unrelated-histories")
            rc, stdout, stderr = await _run_git(
                "pull", "--rebase=false", "--allow-unrelated-histories", "--no-edit",
                "origin", "main",
                cwd=workspace_path, pat=pat,
            )
            if rc != 0 or "CONFLICT" in (stdout or ""):
                logfire.warn("git_sync: conflicts after unrelated-histories merge, committing as-is")
                await _run_git("add", "-A", cwd=workspace_path)
                await _run_git(
                    "commit", "-m", "mcp: commit conflicted files from unrelated-histories merge",
                    cwd=workspace_path,
                )
                rc = 0

        if rc != 0:
            logfire.error(f"git_sync: pull failed (non-fatal): {stderr}")

        await _commit_and_push_dirty(workspace_path, pat)
        return

    has_files = any(
        p.name != ".git" for p in workspace_path.iterdir()
    ) if workspace_path.exists() else False

    if not has_files:
        logfire.info("git_sync: Empty workspace, cloning repo")
        rc, stdout, stderr = await _run_git(
            "clone", _remote_url(pat), str(workspace_path),
            cwd=workspace_path.parent, pat=pat,
        )
        if rc != 0:
            logfire.info(f"git_sync: Clone failed ({stderr.strip()}), initializing fresh repo")
            await _run_git("init", cwd=workspace_path)
            await _configure_repo(workspace_path, pat)
            await _run_git("checkout", "-b", "main", cwd=workspace_path)
        else:
            await _configure_repo(workspace_path, pat)
    else:
        logfire.info("git_sync: Workspace has files, initializing git and connecting to remote")
        await _run_git("init", cwd=workspace_path)
        await _configure_repo(workspace_path, pat)
        await _run_git("checkout", "-b", "main", cwd=workspace_path)
        rc, _, _ = await _run_git("fetch", "origin", cwd=workspace_path, pat=pat)
        if rc == 0:
            rc2, _, stderr2 = await _run_git(
                "merge", "origin/main", "--allow-unrelated-histories", "--no-edit",
                cwd=workspace_path, pat=pat,
            )
            if rc2 != 0:
                logfire.warn(f"git_sync: merge failed (will commit local state): {stderr2}")

    logfire.info("git_sync: ensure_repo complete")


async def commit_and_push(workspace_path: Path) -> None:
    """Stage all changes, commit, and push. Fire-and-forget after each edit.

      - PAT not set or no changes → no-op
      - Pulls first to pick up edits made on GitHub or by the sernia_ai agent
      - If pull has merge conflicts, commits conflicted files as-is
      - Uses asyncio.Lock to serialize concurrent calls within this process
    """
    pat = _get_pat()
    if not pat:
        return

    git_dir = workspace_path / ".git"
    if not git_dir.exists():
        return

    async with _push_lock:
        try:
            rc, stdout, _ = await _run_git("status", "--porcelain", cwd=workspace_path)
            if rc != 0:
                return

            has_changes = bool(stdout.strip())

            rc, _, stderr = await _run_git(
                "pull", "--rebase=false", "origin", "main",
                cwd=workspace_path, pat=pat,
            )
            if rc != 0:
                if "unmerged files" in stderr:
                    logfire.warn("git_sync: unmerged files detected, committing as-is to unblock")
                    await _run_git("add", "-A", cwd=workspace_path)
                    await _run_git(
                        "commit", "--allow-empty", "-m", "mcp: commit unmerged files",
                        cwd=workspace_path,
                    )
                    rc, _, stderr = await _run_git(
                        "pull", "--rebase=false", "origin", "main",
                        cwd=workspace_path, pat=pat,
                    )

                if rc != 0 and "unrelated histories" in stderr:
                    logfire.warn("git_sync: unrelated histories, retrying pull")
                    rc, _, stderr = await _run_git(
                        "pull", "--rebase=false", "--allow-unrelated-histories", "--no-edit",
                        "origin", "main",
                        cwd=workspace_path, pat=pat,
                    )
                    rc_status, status_out, _ = await _run_git(
                        "status", "--porcelain", cwd=workspace_path,
                    )
                    if "U" in (status_out or ""):
                        logfire.warn("git_sync: conflicts after merge, committing as-is")
                        await _run_git("add", "-A", cwd=workspace_path)
                        await _run_git(
                            "commit", "-m", "mcp: commit conflicted files from merge",
                            cwd=workspace_path,
                        )
                        rc = 0

                if rc != 0:
                    logfire.error(f"git_sync: pull before push failed: {stderr}")

            rc, stdout, _ = await _run_git("status", "--porcelain", cwd=workspace_path)
            if rc != 0 or not stdout.strip():
                if not has_changes:
                    return
                rc_log, log_out, _ = await _run_git(
                    "status", "--porcelain", cwd=workspace_path,
                )
                if not log_out.strip():
                    rc_ahead, ahead_out, _ = await _run_git(
                        "rev-list", "--count", "origin/main..HEAD",
                        cwd=workspace_path, pat=pat,
                    )
                    if rc_ahead != 0 or ahead_out.strip() == "0":
                        return

            changed_files = [
                line.split(maxsplit=1)[-1].strip()
                for line in stdout.strip().splitlines()
                if line.strip()
            ] if stdout.strip() else []

            if changed_files:
                file_summary = ", ".join(changed_files[:5])
                if len(changed_files) > 5:
                    file_summary += f" (+{len(changed_files) - 5} more)"
                commit_msg = f"mcp: update {file_summary}"
            else:
                commit_msg = "mcp: sync workspace"

            await _run_git("add", "-A", cwd=workspace_path)

            rc, staged, _ = await _run_git("diff", "--cached", "--quiet", cwd=workspace_path)
            if rc == 0:
                rc_ahead, ahead_out, _ = await _run_git(
                    "rev-list", "--count", "origin/main..HEAD",
                    cwd=workspace_path, pat=pat,
                )
                if rc_ahead != 0 or ahead_out.strip() == "0":
                    return
            else:
                await _run_git("commit", "-m", commit_msg, cwd=workspace_path)

            rc, _, stderr = await _run_git(
                "push", "-u", "origin", "main",
                cwd=workspace_path, pat=pat,
            )
            if rc != 0:
                logfire.error(f"git_sync: push failed: {stderr}")
            else:
                logfire.info("git_sync: pushed successfully")

        except Exception:
            logfire.exception("git_sync: commit_and_push error")
