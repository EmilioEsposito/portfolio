"""Real-git integration tests for the ``git_sync`` flow.

These spawn ``git`` against actual local repos rather than mocking the
subprocess calls. The flow has enough subtle ordering + state-machine logic
that mocked tests can't catch ordering regressions reliably; spinning up two
real repos (a "remote" bare repo and a "local" clone) gives confidence the
real Railway / dev behavior matches what the tests pin.

The two load-bearing properties under test:

  1. **commit-before-pull ordering.** A dirty working tree must NOT block
     the sync — local changes get staged + committed before pulling, so
     pull operates on a clean tree. This is the regression path that lost
     a memory edit on the deployed service (Logfire issue #86 timeline).
  2. **Conflict-marker preservation.** Knowledge repo conflicts ride into
     the merge commit with markers intact, then push to the remote — agents
     and humans read the markers later and decide how to resolve.

All tests share the same fixture pattern: bare remote + working clone +
PAT injected via env var so ``commit_and_push`` doesn't no-op.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

import pytest


def _git(cwd: Path, *args: str) -> str:
    """Synchronous git helper for test setup. Returns stdout (stripped)."""
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _git_log(cwd: Path) -> list[str]:
    return _git(cwd, "log", "--format=%s", "--all").splitlines()


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.fixture
def two_repos(tmp_path, monkeypatch):
    """Set up a bare 'remote' + a working 'local' clone.

    The working clone has identity configured so ``commit_and_push`` can make
    commits via the real git CLI. ``GITHUB_EMILIO_PERSONAL_WRITE_PAT`` is set
    to a dummy value so ``_get_pat()`` returns truthy and the function runs.
    """
    remote = tmp_path / "remote.git"
    local = tmp_path / "local"

    _git(tmp_path, "init", "--bare", str(remote))

    _git(tmp_path, "clone", str(remote), str(local))
    _git(local, "config", "user.email", "test@example.com")
    _git(local, "config", "user.name", "Test")
    _git(local, "checkout", "-b", "main")

    # Seed the remote with an initial commit so subsequent pulls have a base.
    seed = local / "seed.md"
    seed.write_text("seed\n", encoding="utf-8")
    _git(local, "add", "seed.md")
    _git(local, "commit", "-m", "seed")
    _git(local, "push", "-u", "origin", "main")

    monkeypatch.setenv("GITHUB_EMILIO_PERSONAL_WRITE_PAT", "fake-pat")

    return {"remote": remote, "local": local}


def _override_remote_url(local: Path) -> None:
    """The git_sync code resets origin URL to https://<PAT>@github.com/<REPO>.

    For tests we need to override that with the file-system path of the bare
    remote so git can actually fetch/push. Patch the module-level helper.
    """
    import sernia_mcp.clients.git_sync as gs

    remote_path = local.parent / "remote.git"

    def _local_remote_url(_pat: str) -> str:
        return str(remote_path)

    # Monkeypatching at the module level — caller is responsible for cleanup.
    gs._remote_url = _local_remote_url  # type: ignore[assignment]


@pytest.fixture
def patched_remote(two_repos, monkeypatch):
    """Patch ``_remote_url`` to point at the local bare remote."""
    import sernia_mcp.clients.git_sync as gs

    original = gs._remote_url
    remote_path = two_repos["remote"]
    monkeypatch.setattr(gs, "_remote_url", lambda _pat: str(remote_path))
    yield two_repos
    monkeypatch.setattr(gs, "_remote_url", original)


# ============================================================================
# Property 1: commit-before-pull ordering
# ============================================================================

@pytest.mark.asyncio
async def test_dirty_working_tree_does_not_block_sync(patched_remote):
    """The bug from Logfire issue #86 timeline: a dirty working tree caused
    pull to fail with "would be overwritten" and the local edit got stuck.

    With the fix: dirty changes get committed first, pull merges cleanly,
    push goes through. The local change MUST end up on the remote.
    """
    from sernia_mcp.clients.git_sync import commit_and_push

    local = patched_remote["local"]
    remote = patched_remote["remote"]

    # Make a local edit but DON'T commit it.
    (local / "MEMORY.md").write_text("local edit\n", encoding="utf-8")

    await commit_and_push(local)

    # The bare remote should now have a commit containing the local edit.
    # Use a sibling clone to verify, since we can't `cat` from a bare repo.
    verify = local.parent / "verify"
    _git(local.parent, "clone", str(remote), str(verify))
    assert (verify / "MEMORY.md").read_text() == "local edit\n"


@pytest.mark.asyncio
async def test_dirty_local_plus_remote_ahead_succeeds(patched_remote):
    """The exact issue-#86 scenario: local working tree dirty AND remote
    moved ahead with a non-conflicting change. Both changes must end up on
    the remote with neither lost.
    """
    from sernia_mcp.clients.git_sync import commit_and_push

    local = patched_remote["local"]
    remote = patched_remote["remote"]

    # Remote moves ahead with a separate file.
    other = local.parent / "other_clone"
    _git(local.parent, "clone", str(remote), str(other))
    _git(other, "config", "user.email", "test@example.com")
    _git(other, "config", "user.name", "Test")
    (other / "REMOTE_FILE.md").write_text("from remote\n", encoding="utf-8")
    _git(other, "add", "REMOTE_FILE.md")
    _git(other, "commit", "-m", "remote: add REMOTE_FILE.md")
    _git(other, "push", "origin", "main")

    # Local has a dirty edit to a different file (no conflict).
    (local / "MEMORY.md").write_text("local edit\n", encoding="utf-8")

    await commit_and_push(local)

    # Remote should have BOTH files at HEAD.
    verify = local.parent / "verify"
    _git(local.parent, "clone", str(remote), str(verify))
    assert (verify / "MEMORY.md").read_text() == "local edit\n"
    assert (verify / "REMOTE_FILE.md").read_text() == "from remote\n"


@pytest.mark.asyncio
async def test_no_local_changes_no_op(patched_remote):
    """When the working tree is clean and there's nothing to push, the
    function should do nothing (no spurious empty commits).
    """
    from sernia_mcp.clients.git_sync import commit_and_push

    local = patched_remote["local"]
    before = _git_log(local)

    await commit_and_push(local)

    after = _git_log(local)
    assert before == after, "commit_and_push should be a no-op on clean tree"


# ============================================================================
# Property 2: conflict-marker preservation
# ============================================================================

@pytest.mark.asyncio
async def test_conflict_markers_preserved_through_to_remote(patched_remote):
    """When local and remote modify the same file in incompatible ways, the
    pull merge produces conflict markers (``<<<<<<<`` etc). For a knowledge
    repo the markers are informative — they MUST land on the remote so the
    agent or a human can read them later and resolve.
    """
    from sernia_mcp.clients.git_sync import commit_and_push

    local = patched_remote["local"]
    remote = patched_remote["remote"]

    # Remote moves ahead with a change to MEMORY.md.
    other = local.parent / "other_clone"
    _git(local.parent, "clone", str(remote), str(other))
    _git(other, "config", "user.email", "test@example.com")
    _git(other, "config", "user.name", "Test")
    (other / "MEMORY.md").write_text("REMOTE VERSION\n", encoding="utf-8")
    _git(other, "add", "MEMORY.md")
    _git(other, "commit", "-m", "remote: change MEMORY")
    _git(other, "push", "origin", "main")

    # Local makes an incompatible edit to the same file.
    (local / "MEMORY.md").write_text("LOCAL VERSION\n", encoding="utf-8")

    await commit_and_push(local)

    # The merge commit on the remote should contain conflict markers.
    verify = local.parent / "verify"
    _git(local.parent, "clone", str(remote), str(verify))
    pushed = (verify / "MEMORY.md").read_text()
    assert "<<<<<<<" in pushed, f"missing conflict markers in pushed file:\n{pushed}"
    assert "=======" in pushed
    assert ">>>>>>>" in pushed
    assert "LOCAL VERSION" in pushed
    assert "REMOTE VERSION" in pushed


# ============================================================================
# Helper unit test: _has_unmerged_files
# ============================================================================

def test_has_unmerged_files_recognizes_porcelain_codes():
    """``UU file`` etc are unmerged. ``M  file`` / `` M file`` etc are not."""
    from sernia_mcp.clients.git_sync import _has_unmerged_files

    # Unmerged status codes
    assert _has_unmerged_files("UU MEMORY.md") is True
    assert _has_unmerged_files("AA MEMORY.md") is True
    assert _has_unmerged_files("DD MEMORY.md") is True
    assert _has_unmerged_files("AU MEMORY.md") is True
    assert _has_unmerged_files("UA MEMORY.md") is True
    assert _has_unmerged_files("UD MEMORY.md") is True
    assert _has_unmerged_files("DU MEMORY.md") is True

    # Non-unmerged status codes
    assert _has_unmerged_files("M  MEMORY.md") is False
    assert _has_unmerged_files(" M MEMORY.md") is False
    assert _has_unmerged_files("A  new.md") is False
    assert _has_unmerged_files("?? untracked.md") is False
    assert _has_unmerged_files("") is False
    assert _has_unmerged_files("\n") is False


@pytest.mark.asyncio
async def test_failed_push_emits_logfire_exception(monkeypatch, patched_remote):
    """If a local commit fails to push, ``logfire.exception`` MUST fire so
    operators get paged via the Logfire Issues → Slack pipeline. The error
    must NOT propagate to the caller (fire-and-forget contract).

    Why an exception, not error(): ``logfire.error()`` is a silent log
    line; ``logfire.exception()`` creates a tracked Issue (per the user's
    Logfire conventions documented in user memory). Local commits that
    don't reach the remote are at risk of permanent loss on the next
    Railway redeploy — exactly the kind of thing that should page.
    """
    from unittest.mock import patch

    import sernia_mcp.clients.git_sync as gs

    local = patched_remote["local"]

    # Make a real local edit so commit_and_push has something to push.
    (local / "MEMORY.md").write_text("local edit\n", encoding="utf-8")

    # Sabotage just the push step — everything else uses the real bare
    # remote so the prior steps (status, add, commit, pull, rev-list)
    # behave normally and produce a genuine "ahead of remote" state.
    original_run_git = gs._run_git

    async def fail_on_push(*args, **kwargs):
        if args and args[0] == "push":
            return 1, "", "remote rejected (simulated for test)"
        return await original_run_git(*args, **kwargs)

    monkeypatch.setattr(gs, "_run_git", fail_on_push)

    with patch.object(gs.logfire, "exception") as fake_exc:
        # Must not raise — fire-and-forget contract.
        await gs.commit_and_push(local)

    fake_exc.assert_called_once()
    call = fake_exc.call_args
    assert "did not reach remote" in call.args[0].lower()
    assert call.kwargs.get("ahead_count", 0) >= 1
    assert "remote rejected" in call.kwargs.get("stderr", "")


@pytest.mark.asyncio
async def test_successful_push_does_not_emit_logfire_exception(
    monkeypatch, patched_remote
):
    """The happy path must NOT fire logfire.exception — we don't want
    every successful push creating an Issue + Slack ping."""
    from unittest.mock import patch

    import sernia_mcp.clients.git_sync as gs

    local = patched_remote["local"]
    (local / "MEMORY.md").write_text("local edit\n", encoding="utf-8")

    with patch.object(gs.logfire, "exception") as fake_exc:
        await gs.commit_and_push(local)

    fake_exc.assert_not_called()


# ============================================================================
# pull_workspace (the pre-read freshening primitive)
# ============================================================================

@pytest.mark.asyncio
async def test_pull_workspace_picks_up_remote_changes(patched_remote):
    """When the remote has commits the local doesn't, pull_workspace
    brings them in. This is the core drift-prevention property."""
    from sernia_mcp.clients.git_sync import pull_workspace

    local = patched_remote["local"]
    remote = patched_remote["remote"]

    # Move the remote ahead via a sibling clone.
    other = local.parent / "other_clone"
    _git(local.parent, "clone", str(remote), str(other))
    _git(other, "config", "user.email", "test@example.com")
    _git(other, "config", "user.name", "Test")
    (other / "REMOTE_FILE.md").write_text("from remote\n", encoding="utf-8")
    _git(other, "add", "REMOTE_FILE.md")
    _git(other, "commit", "-m", "remote: add REMOTE_FILE.md")
    _git(other, "push", "origin", "main")

    # Local doesn't know about REMOTE_FILE.md yet
    assert not (local / "REMOTE_FILE.md").exists()

    await pull_workspace(local)

    # After pull_workspace, the remote file IS visible locally
    assert (local / "REMOTE_FILE.md").read_text() == "from remote\n"


@pytest.mark.asyncio
async def test_pull_workspace_does_not_push(patched_remote):
    """pull_workspace must commit dirty local edits (so pull doesn't fail)
    but must NOT push them. Push is reserved for commit_and_push, triggered
    by explicit edits — not by pre-read freshening.
    """
    from sernia_mcp.clients.git_sync import pull_workspace

    local = patched_remote["local"]
    remote = patched_remote["remote"]

    # Local has a dirty edit
    (local / "MEMORY.md").write_text("local edit\n", encoding="utf-8")

    await pull_workspace(local)

    # Local commit was made (so pull could merge)
    rc, log_out, _ = (
        0,
        subprocess.run(
            ["git", "log", "--format=%s"], cwd=local,
            check=True, capture_output=True, text=True,
        ).stdout.strip(),
        "",
    )
    assert "pre-pull commit" in log_out

    # But the remote does NOT have the local edit (no push happened)
    verify = local.parent / "verify_no_push"
    _git(local.parent, "clone", str(remote), str(verify))
    assert not (verify / "MEMORY.md").exists() or (
        (verify / "MEMORY.md").read_text() != "local edit\n"
    )


@pytest.mark.asyncio
async def test_pull_workspace_clean_tree_no_op(patched_remote):
    """Clean local + remote in sync → no-op. No spurious commits."""
    from sernia_mcp.clients.git_sync import pull_workspace

    local = patched_remote["local"]
    before = _git_log(local)

    await pull_workspace(local)

    after = _git_log(local)
    assert before == after


@pytest.mark.asyncio
async def test_pull_workspace_skips_when_no_pat(monkeypatch, tmp_path):
    """No PAT → silent no-op."""
    from sernia_mcp.clients import git_sync

    monkeypatch.delenv("GITHUB_EMILIO_PERSONAL_WRITE_PAT", raising=False)

    calls = []

    async def fake_run_git(*args, **kwargs):
        calls.append(args)
        return 0, "", ""

    monkeypatch.setattr(git_sync, "_run_git", fake_run_git)
    await git_sync.pull_workspace(tmp_path)

    assert calls == []


@pytest.mark.asyncio
async def test_pull_workspace_failure_does_not_raise(monkeypatch, patched_remote):
    """Pull failures must NOT raise (fail-soft) — drift is acceptable;
    blocking the read path is not.
    """
    from unittest.mock import patch

    import sernia_mcp.clients.git_sync as gs

    local = patched_remote["local"]

    original_run_git = gs._run_git

    async def fail_on_pull(*args, **kwargs):
        if args and args[0] == "pull":
            return 1, "", "simulated network failure"
        return await original_run_git(*args, **kwargs)

    monkeypatch.setattr(gs, "_run_git", fail_on_pull)

    # Must not raise
    with patch.object(gs.logfire, "warn") as fake_warn:
        await gs.pull_workspace(local)

    # Should have logged a warning about the failure
    assert any(
        "pull_workspace failed" in str(call) or "non-fatal" in str(call)
        for call in fake_warn.call_args_list
    )


@pytest.mark.asyncio
async def test_skips_when_no_pat(monkeypatch, tmp_path):
    """No PAT → silent no-op, no exceptions, no git invocations."""
    from sernia_mcp.clients import git_sync

    monkeypatch.delenv("GITHUB_EMILIO_PERSONAL_WRITE_PAT", raising=False)

    # Should not raise, should not call _run_git.
    calls = []

    async def fake_run_git(*args, **kwargs):
        calls.append(args)
        return 0, "", ""

    monkeypatch.setattr(git_sync, "_run_git", fake_run_git)
    await git_sync.commit_and_push(tmp_path)

    assert calls == [], "expected no git invocations when PAT is unset"


@pytest.mark.asyncio
async def test_skips_when_not_a_git_repo(monkeypatch, tmp_path):
    """Workspace exists but isn't a git repo → no-op."""
    from sernia_mcp.clients import git_sync

    monkeypatch.setenv("GITHUB_EMILIO_PERSONAL_WRITE_PAT", "fake-pat")

    calls = []

    async def fake_run_git(*args, **kwargs):
        calls.append(args)
        return 0, "", ""

    monkeypatch.setattr(git_sync, "_run_git", fake_run_git)
    await git_sync.commit_and_push(tmp_path)  # no .git/ subdir

    assert calls == [], "expected no git invocations when not a git repo"
