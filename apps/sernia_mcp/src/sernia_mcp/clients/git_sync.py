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


class GitSyncPushFailed(Exception):
    """Local commits exist but failed to propagate to the remote repo.

    Raised+caught locally inside ``commit_and_push`` to drive a
    ``logfire.exception`` call. The fire-and-forget task wrapper around
    ``commit_and_push`` already discards exceptions, so this never reaches
    the MCP client — but the Logfire Issue + Slack notification it produces
    surfaces the data-loss risk to operators.
    """


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


async def _stage_and_commit_dirty(workspace: Path, commit_msg: str) -> bool:
    """Stage all changes and commit. Returns True if a commit was created.

    Used as the first step of any sync to turn local working-tree changes
    into a real commit before pulling — pulling onto a dirty tree fails with
    "Your local changes would be overwritten by merge" and stalls the sync.
    Also used to stage post-pull conflict markers as-is (the markers ride
    into the merge commit; they're informative, not corrupt state).
    """
    rc, stdout, _ = await _run_git("status", "--porcelain", cwd=workspace)
    if rc != 0 or not stdout.strip():
        return False
    await _run_git("add", "-A", cwd=workspace)
    rc, _, _ = await _run_git("diff", "--cached", "--quiet", cwd=workspace)
    if rc == 0:
        return False  # nothing staged after add (e.g. only .gitignored files)
    await _run_git("commit", "-m", commit_msg, cwd=workspace)
    return True


def _has_unmerged_files(status_out: str) -> bool:
    """True if ``git status --porcelain`` shows any unmerged entries."""
    for line in (status_out or "").splitlines():
        if not line:
            continue
        # XY status codes for unmerged: DD AU UD UA DU AA UU
        x, y = line[:1], line[1:2]
        if "U" in (x, y) or (x == "A" and y == "A") or (x == "D" and y == "D"):
            return True
    return False


async def _commit_and_push_dirty(workspace: Path, pat: str) -> None:
    """Commit and push any uncommitted workspace changes. Called once at startup."""
    committed = await _stage_and_commit_dirty(
        workspace, "mcp: commit uncommitted changes from previous run"
    )
    if not committed:
        return
    logfire.info("git_sync: found uncommitted changes on startup, committing")
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
    """Stage all changes, commit, pull, and push. Fire-and-forget after each edit.

    Order matters:

      1. **Stage + commit local changes FIRST.** Pulling onto a dirty working
         tree fails with "Your local changes would be overwritten by merge,"
         which previously left the local state stuck and the remote unchanged.
         Committing first turns the local mutation into a real commit that
         pull can merge with cleanly.

      2. **Pull (no rebase, no edit).** Picks up edits made on GitHub or by
         the sernia_ai agent. Auto-merge handles non-conflicting concurrent
         changes. ``--allow-unrelated-histories`` retry handles the rare
         "fresh repo on each side" case.

      3. **Commit conflict markers as-is.** This is a knowledge-only repo:
         a merge with conflicts leaves ``<<<<<<<`` markers in the file, and
         we deliberately commit them as-is so the agent (or a human) can
         read the markers later and decide how to resolve. The alternative —
         aborting the merge — leaves the workspace in an inconsistent state.

      4. **Push.** Skipped if local is not ahead of origin.

    No-ops cleanly when:
      - PAT is not set (local-only mode)
      - The workspace isn't a git repo (``ensure_repo`` skipped)
      - There's nothing to commit AND nothing local-ahead of remote

    Uses ``asyncio.Lock`` to serialize concurrent calls within this process.
    Cross-process / cross-service races are handled by the pull-merge-push
    pattern itself.
    """
    pat = _get_pat()
    if not pat:
        return

    git_dir = workspace_path / ".git"
    if not git_dir.exists():
        return

    async with _push_lock:
        try:
            # 1. Stage + commit any local changes BEFORE pulling, so pull
            # operates on a clean working tree.
            rc, status_out, _ = await _run_git(
                "status", "--porcelain", cwd=workspace_path,
            )
            if rc != 0:
                return

            if status_out.strip():
                changed_files = [
                    line.split(maxsplit=1)[-1].strip()
                    for line in status_out.strip().splitlines()
                    if line.strip()
                ]
                file_summary = ", ".join(changed_files[:5])
                if len(changed_files) > 5:
                    file_summary += f" (+{len(changed_files) - 5} more)"
                commit_msg = f"mcp: update {file_summary}"
                await _stage_and_commit_dirty(workspace_path, commit_msg)

            # 2. Pull. Local commits + remote commits merge here.
            rc, _, stderr = await _run_git(
                "pull", "--rebase=false", "--no-edit", "origin", "main",
                cwd=workspace_path, pat=pat,
            )
            if rc != 0 and "unrelated histories" in stderr:
                logfire.warn("git_sync: unrelated histories, retrying pull")
                rc, _, stderr = await _run_git(
                    "pull", "--rebase=false", "--allow-unrelated-histories", "--no-edit",
                    "origin", "main",
                    cwd=workspace_path, pat=pat,
                )
            if rc != 0:
                logfire.error(f"git_sync: pull failed: {stderr}")

            # 3. If the merge left conflict markers, commit them as-is.
            # In a knowledge-only repo the markers are informative — the
            # agent or human reads them later and decides how to resolve.
            rc, status_out, _ = await _run_git(
                "status", "--porcelain", cwd=workspace_path,
            )
            if rc == 0 and _has_unmerged_files(status_out):
                logfire.warn(
                    "git_sync: conflict markers preserved as-is for human/agent review"
                )
                await _stage_and_commit_dirty(
                    workspace_path,
                    "mcp: commit conflicted files from merge (markers preserved)",
                )

            # 4. Push, but only if we have local commits ahead of remote.
            rc_ahead, ahead_out, _ = await _run_git(
                "rev-list", "--count", "origin/main..HEAD",
                cwd=workspace_path, pat=pat,
            )
            if rc_ahead != 0 or ahead_out.strip() == "0":
                return

            try:
                ahead_count = int(ahead_out.strip())
            except ValueError:
                ahead_count = -1

            rc, _, stderr = await _run_git(
                "push", "-u", "origin", "main",
                cwd=workspace_path, pat=pat,
            )
            if rc == 0:
                logfire.info("git_sync: pushed successfully")
                return

            # Push failed AND we had local commits — local edits did not
            # propagate to the remote. On Railway, the next redeploy will
            # wipe the local filesystem and lose them. Fail LOUDLY via
            # logfire.exception (creates a Logfire Issue, triggers Slack
            # notification) so an operator sees this. We deliberately do
            # NOT propagate the exception to the caller — edit_resource
            # already returned success to the model, and the fire-and-forget
            # contract is "best-effort sync."
            try:
                raise GitSyncPushFailed(
                    f"local commits failed to push to remote — "
                    f"{ahead_count} commit(s) at risk of loss on next "
                    f"workspace redeploy. push stderr: {stderr}"
                )
            except GitSyncPushFailed:
                logfire.exception(
                    "git_sync: local edits did not reach remote",
                    ahead_count=ahead_count,
                    stderr=stderr,
                )

        except Exception:
            logfire.exception("git_sync: commit_and_push error")
