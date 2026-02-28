"""
Git-backed sync for the Sernia AI agent workspace.

Backs the .workspace/ directory with a private GitHub repo so that:
- File edits are visible as git commits on GitHub
- Knowledge can be edited directly on GitHub
- Version history comes for free

Requires GITHUB_EMILIO_PERSONAL_WRITE_PAT env var. Without it, workspace
operates in local-only mode (no git sync).
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

    # Redact PAT in logs
    if pat:
        stdout = _redact(stdout, pat)
        stderr = _redact(stderr, pat)

    return proc.returncode, stdout, stderr


async def _configure_repo(workspace: Path, pat: str) -> None:
    """Set repo-local git config for the AI agent."""
    await _run_git("config", "user.name", "Sernia AI Agent", cwd=workspace)
    await _run_git("config", "user.email", "ai-agent@serniacapital.com", cwd=workspace)
    # Ensure remote URL uses current PAT
    rc, _, _ = await _run_git("remote", "get-url", "origin", cwd=workspace, pat=pat)
    if rc == 0:
        await _run_git("remote", "set-url", "origin", _remote_url(pat), cwd=workspace, pat=pat)
    else:
        await _run_git("remote", "add", "origin", _remote_url(pat), cwd=workspace, pat=pat)


async def ensure_repo(workspace_path: Path) -> None:
    """
    Ensure workspace is backed by the git repo. Called once at startup.

    Scenarios:
    - PAT not set -> no-op (local-only fallback)
    - Not a git repo, dir empty -> git clone
    - Not a git repo, dir has files -> git init + add remote + fetch
    - Already a git repo -> git pull (non-fatal on failure)
    """
    pat = _get_pat()
    if not pat:
        logfire.info("git_sync: No PAT set, workspace will be local-only")
        return

    workspace_path.mkdir(parents=True, exist_ok=True)
    git_dir = workspace_path / ".git"
    is_git_repo = git_dir.exists()

    if is_git_repo:
        # Already a git repo - just pull latest
        logfire.info("git_sync: Existing repo, pulling latest")
        await _configure_repo(workspace_path, pat)
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
        if rc != 0:
            logfire.error(f"git_sync: pull failed (non-fatal): {stderr}")
        return

    # Check if directory has existing files (besides .git)
    has_files = any(
        p.name != ".git" for p in workspace_path.iterdir()
    ) if workspace_path.exists() else False

    if not has_files:
        # Empty directory - clone directly
        logfire.info("git_sync: Empty workspace, cloning repo")
        # Clone into a temp name, then move contents
        # (git clone won't clone into non-empty dir)
        rc, stdout, stderr = await _run_git(
            "clone", _remote_url(pat), str(workspace_path),
            cwd=workspace_path.parent, pat=pat,
        )
        if rc != 0:
            # Clone may fail if repo is empty (no commits yet)
            # In that case, init fresh
            logfire.info(f"git_sync: Clone failed ({stderr.strip()}), initializing fresh repo")
            await _run_git("init", cwd=workspace_path)
            await _configure_repo(workspace_path, pat)
            # Create main branch
            await _run_git("checkout", "-b", "main", cwd=workspace_path)
        else:
            await _configure_repo(workspace_path, pat)
    else:
        # Has existing files - init and connect to remote
        logfire.info("git_sync: Workspace has files, initializing git and connecting to remote")
        await _run_git("init", cwd=workspace_path)
        await _configure_repo(workspace_path, pat)
        await _run_git("checkout", "-b", "main", cwd=workspace_path)
        # Fetch remote to see if there are existing commits
        rc, _, _ = await _run_git("fetch", "origin", cwd=workspace_path, pat=pat)
        if rc == 0:
            # Try to merge remote history (may fail if unrelated histories)
            rc2, _, stderr2 = await _run_git(
                "merge", "origin/main", "--allow-unrelated-histories", "--no-edit",
                cwd=workspace_path, pat=pat,
            )
            if rc2 != 0:
                logfire.warn(f"git_sync: merge failed (will commit local state): {stderr2}")

    logfire.info("git_sync: ensure_repo complete")


async def commit_and_push(workspace_path: Path) -> None:
    """
    Stage all changes, commit, and push. Fire-and-forget after each agent turn.

    - PAT not set or no changes -> no-op
    - Pulls first to pick up GitHub edits
    - If pull has merge conflicts, commits conflicted files as-is
    - Uses asyncio.Lock to serialize concurrent calls
    """
    pat = _get_pat()
    if not pat:
        return

    git_dir = workspace_path / ".git"
    if not git_dir.exists():
        return

    async with _push_lock:
        try:
            # Check for changes first
            rc, stdout, _ = await _run_git("status", "--porcelain", cwd=workspace_path)
            if rc != 0:
                return

            has_changes = bool(stdout.strip())

            # Pull first to pick up edits made on GitHub
            rc, _, stderr = await _run_git(
                "pull", "--rebase=false", "origin", "main",
                cwd=workspace_path, pat=pat,
            )
            if rc != 0:
                if "unrelated histories" in stderr:
                    # Local repo diverged from remote — retry allowing unrelated histories
                    logfire.warn("git_sync: unrelated histories, retrying with --allow-unrelated-histories")
                    rc, _, stderr = await _run_git(
                        "pull", "--rebase=false", "--allow-unrelated-histories", "--no-edit",
                        "origin", "main",
                        cwd=workspace_path, pat=pat,
                    )
                    if rc != 0:
                        logfire.error(f"git_sync: pull with --allow-unrelated-histories also failed: {stderr}")

                if rc != 0:
                    logfire.error(f"git_sync: pull before push failed: {stderr}")
                    # If merge conflict, stage and commit the conflicted state
                    # The agent will see conflict markers on next run
                    rc_status, status_out, _ = await _run_git(
                        "status", "--porcelain", cwd=workspace_path,
                    )
                    if "U" in (status_out or ""):
                        logfire.warn("git_sync: merge conflicts detected, committing conflicted files")
                        await _run_git("add", "-A", cwd=workspace_path)
                        await _run_git(
                            "commit", "-m", "agent: merge conflict (auto-committed with markers)",
                            cwd=workspace_path,
                        )

            # Re-check for changes after pull
            rc, stdout, _ = await _run_git("status", "--porcelain", cwd=workspace_path)
            if rc != 0 or not stdout.strip():
                if not has_changes:
                    return
                # If we had changes before pull but not after, pull resolved them
                # Still need to push if pull created new merge commit
                rc_log, log_out, _ = await _run_git(
                    "status", "--porcelain", cwd=workspace_path,
                )
                if not log_out.strip():
                    # Check if we have unpushed commits
                    rc_ahead, ahead_out, _ = await _run_git(
                        "rev-list", "--count", "origin/main..HEAD",
                        cwd=workspace_path, pat=pat,
                    )
                    if rc_ahead != 0 or ahead_out.strip() == "0":
                        return

            # Build commit message from changed files
            changed_files = [
                line.split(maxsplit=1)[-1].strip()
                for line in stdout.strip().splitlines()
                if line.strip()
            ] if stdout.strip() else []

            if changed_files:
                file_summary = ", ".join(changed_files[:5])
                if len(changed_files) > 5:
                    file_summary += f" (+{len(changed_files) - 5} more)"
                commit_msg = f"agent: update {file_summary}"
            else:
                commit_msg = "agent: sync workspace"

            # Stage, commit, push
            await _run_git("add", "-A", cwd=workspace_path)

            # Check if there's anything staged to commit
            rc, staged, _ = await _run_git("diff", "--cached", "--quiet", cwd=workspace_path)
            if rc == 0:
                # Nothing staged - check for unpushed commits
                rc_ahead, ahead_out, _ = await _run_git(
                    "rev-list", "--count", "origin/main..HEAD",
                    cwd=workspace_path, pat=pat,
                )
                if rc_ahead != 0 or ahead_out.strip() == "0":
                    return
            else:
                # There are staged changes - commit them
                await _run_git("commit", "-m", commit_msg, cwd=workspace_path)

            # Push
            rc, _, stderr = await _run_git(
                "push", "-u", "origin", "main",
                cwd=workspace_path, pat=pat,
            )
            if rc != 0:
                logfire.error(f"git_sync: push failed: {stderr}")
            else:
                logfire.info(f"git_sync: pushed successfully")

        except Exception:
            logfire.exception("git_sync: commit_and_push error")
