"""Worktree invariants using disposable Git repos and mocked local Postgres calls.

Also runnable without app dependencies: python3 -m unittest discover -s api/src/tests
-p test_worktrees.py. No real third-party services or application database are used.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from unittest.mock import patch

from scripts import worktree as wt


def allocate_path(path: str) -> dict:
    return wt.allocate(wt.Checkout.find(Path(path)))


class WorktreeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.main = self.root / "main"
        self.main.mkdir()
        self.git(self.main, "init", "-b", "main")
        self.git(self.main, "config", "user.email", "tests@example.test")
        self.git(self.main, "config", "user.name", "Worktree tests")
        (self.main / ".gitignore").write_text(".env\nnode_modules/\n")
        (self.main / "file.txt").write_text("base\n")
        self.git(self.main, "add", ".")
        self.git(self.main, "commit", "-m", "Initial")
        self.remote = self.root / "remote.git"
        self.git(self.main, "clone", "--bare", str(self.main), str(self.remote))
        self.git(self.main, "remote", "add", "origin", str(self.remote))
        self.git(self.main, "fetch", "origin")

    @staticmethod
    def git(path: Path, *args: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(path), *args], stderr=subprocess.DEVNULL, text=True
        ).strip()

    def checkout(self, name: str = "feature") -> wt.Checkout:
        path = self.root / name
        self.git(self.main, "worktree", "add", "-b", name.replace("/", "-"), str(path))
        return wt.Checkout.find(path)

    def test_external_same_basename_gets_distinct_stable_resources(self) -> None:
        first, second = self.checkout("codex-one/portfolio"), self.checkout("codex-two/portfolio")
        first_state, second_state = wt.allocate(first), wt.allocate(second)
        self.assertNotEqual(first_state["database"], second_state["database"])
        self.assertNotEqual(first_state["test_database"], second_state["test_database"])
        self.assertNotEqual(first_state["api_port"], second_state["api_port"])
        self.assertLessEqual(len(first_state["test_database"]), 63)
        self.assertEqual(wt.allocate(first), first_state)
        moved = self.root / "renamed"
        self.git(self.main, "worktree", "move", str(first.root), str(moved))
        self.assertEqual(wt.allocate(wt.Checkout.find(moved)), first_state)

    def test_concurrent_provisioning_reserves_different_port_pairs(self) -> None:
        checkouts = [self.checkout(f"task-{i}") for i in range(3)]
        with ProcessPoolExecutor(max_workers=3) as pool:
            states = list(pool.map(allocate_path, [str(c.root) for c in checkouts]))
        self.assertEqual(len({state["api_port"] for state in states}), 3)
        self.assertEqual(len({state["web_port"] for state in states}), 3)

    def test_allocation_avoids_existing_legacy_and_listening_ports(self) -> None:
        legacy = self.checkout("legacy")
        (legacy.root / ".env").write_text("PORT=8010\n")
        new = self.checkout()
        with patch.object(wt, "port_available", side_effect=lambda port: port != 8020):
            state = wt.allocate(new)
        self.assertEqual(state["api_port"], 8030)

    def test_environment_copy_preserves_secrets_and_removes_duplicate_managed_keys(self) -> None:
        source, destination = self.root / "source", self.root / "destination"
        source.write_text("# keep this\nSECRET='private value'\nPORT=8000\nexport PORT=8001\n")
        wt.update_env(source, destination, {"PORT": "8050"})
        self.assertEqual(source.read_text().count("PORT="), 2)
        self.assertEqual(destination.read_text().count("PORT="), 1)
        self.assertIn("SECRET='private value'", destination.read_text())
        destination.write_text(destination.read_text() + "LOCAL_ONLY=keep\n")
        wt.update_env(source, destination, {"PORT": "8050"})
        self.assertIn("LOCAL_ONLY=keep", destination.read_text())
        self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
        destination.unlink()
        destination.symlink_to(source)
        with self.assertRaises(wt.WorktreeError):
            wt.update_env(source, destination, {"PORT": "8050"})
        self.assertIn("PORT=8000", source.read_text())

    def test_once_skips_main_non_git_and_completed_worktrees(self) -> None:
        wt.provision(self.root, once=True)
        wt.provision(self.main, once=True)
        checkout = self.checkout()
        state = wt.allocate(checkout)
        state["provisioned"] = True
        wt.save_state(checkout, state)
        with patch.object(wt, "run_logged") as run:
            wt.provision(checkout.root, once=True)
        run.assert_not_called()

    def test_failed_dependency_install_does_not_mark_ready(self) -> None:
        checkout = self.checkout()
        original = subprocess.run
        with (
            patch.object(wt.shutil, "which", return_value="present"),
            patch.object(wt.subprocess, "run", wraps=subprocess.run) as run,
        ):
            # Version probing is the only non-Git subprocess before the mocked install.
            def command(*args: object, **kwargs: object) -> subprocess.CompletedProcess:
                if args[0] == ["node", "--version"]:
                    return subprocess.CompletedProcess(args[0], 0, stdout="v24.0.0\n")
                return original(*args, **kwargs)

            run.side_effect = command
            with patch.object(wt, "run_logged", side_effect=wt.WorktreeError("install failed")):
                with self.assertRaises(wt.WorktreeError):
                    wt.provision(checkout.root)
        self.assertFalse(wt.read_state(checkout)["provisioned"])

    def test_tampered_state_cannot_target_shared_database(self) -> None:
        checkout = self.checkout()
        state = wt.allocate(checkout)
        state["database"] = "portfolio"
        checkout.state_path.write_text(json.dumps(state))
        with self.assertRaises(wt.WorktreeError), patch.object(wt, "sql") as sql:
            wt.cleanup(checkout)
        sql.assert_not_called()

    def test_existing_unowned_database_is_never_adopted(self) -> None:
        checkout = self.checkout()
        state = wt.allocate(checkout)
        with patch.object(wt, "sql", side_effect=["1", "wrong-owner"]) as sql:
            with self.assertRaises(wt.WorktreeError):
                wt.prepare_database(checkout, state)
        self.assertFalse(any("CREATE" in call.args[0] for call in sql.call_args_list))

    def test_source_schema_check_rejects_revisions_absent_from_branch(self) -> None:
        checkout = self.checkout()
        versions = checkout.root / "api/src/database/migrations/versions"
        versions.mkdir(parents=True)
        (versions / "one.py").write_text("revision: str = 'known'\n")
        with patch.object(wt, "sql", side_effect=["alembic_version", "old-removed-revision"]):
            self.assertFalse(wt.source_schema_compatible(checkout))
        with patch.object(wt, "sql", side_effect=["alembic_version", "known"]):
            self.assertTrue(wt.source_schema_compatible(checkout))

    def test_divergent_source_uses_fresh_database_without_dumping(self) -> None:
        checkout = self.checkout()
        state = wt.allocate(checkout)
        with (
            patch.object(wt, "sql", side_effect=["", "", "", "1"]),
            patch.object(wt, "source_schema_compatible", return_value=False),
            patch.object(wt.subprocess, "run") as run,
            patch.object(wt, "run_logged") as migrate,
        ):
            wt.prepare_database(checkout, state)
        run.assert_not_called()
        self.assertEqual(migrate.call_count, 2)
        self.assertTrue(wt.read_state(checkout)[state["database"] + "_initialized"])

    def test_failed_snapshot_is_not_marked_initialized(self) -> None:
        checkout = self.checkout()
        state = wt.allocate(checkout)
        with (
            patch.object(wt, "sql", side_effect=["", "", "", "1"]),
            patch.object(wt, "source_schema_compatible", return_value=True),
            patch.object(wt.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)),
        ):
            with self.assertRaises(wt.WorktreeError):
                wt.prepare_database(checkout, state)
        self.assertNotIn(state["database"] + "_initialized", wt.read_state(checkout))

    def test_busy_second_database_prevents_any_drop(self) -> None:
        checkout = self.checkout()
        state = wt.allocate(checkout)
        owner = "portfolio-worktree:" + state["owner"]
        with patch.object(wt, "sql", side_effect=["1", owner, "", "1", owner, "1"]) as sql:
            with self.assertRaises(wt.WorktreeError):
                wt.cleanup(checkout)
        self.assertFalse(any("DROP" in call.args[0] for call in sql.call_args_list))

    def test_cleanup_refuses_active_database(self) -> None:
        checkout = self.checkout()
        state = wt.allocate(checkout)
        with patch.object(
            wt, "sql", side_effect=["1", "portfolio-worktree:" + state["owner"], "1"]
        ) as sql:
            with self.assertRaises(wt.WorktreeError):
                wt.cleanup(checkout)
        self.assertFalse(any("DROP" in call.args[0] for call in sql.call_args_list))

    def test_cleanup_dry_run_has_no_database_calls(self) -> None:
        checkout = self.checkout()
        wt.allocate(checkout)
        with patch.object(wt, "sql") as sql:
            wt.cleanup(checkout, dry_run=True)
        sql.assert_not_called()

    def test_dirty_worktree_cannot_release_resources(self) -> None:
        checkout = self.checkout()
        wt.allocate(checkout)
        (checkout.root / "unsaved.txt").write_text("valuable work")
        with patch.object(wt, "cleanup") as cleanup:
            with self.assertRaises(wt.WorktreeError):
                wt.remove(checkout)
        cleanup.assert_not_called()
        self.assertTrue(checkout.root.exists())

    def test_unmerged_clean_commits_cannot_be_removed(self) -> None:
        checkout = self.checkout()
        (checkout.root / "file.txt").write_text("new work")
        self.git(checkout.root, "commit", "-am", "Unmerged")
        with patch.object(wt, "cleanup") as cleanup:
            with self.assertRaises(wt.WorktreeError):
                wt.remove(checkout)
        cleanup.assert_not_called()

    def test_new_commit_after_merge_marker_blocks_cleanup(self) -> None:
        checkout = self.checkout()
        state = wt.allocate(checkout)
        state["cleanup"] = {
            "merge_commit": self.git(self.main, "rev-parse", "HEAD"),
            "head": self.git(checkout.root, "rev-parse", "HEAD"),
        }
        wt.save_state(checkout, state)
        (checkout.root / "file.txt").write_text("more work")
        self.git(checkout.root, "commit", "-am", "Later work")
        with patch.object(wt, "cleanup") as cleanup:
            with self.assertRaises(wt.WorktreeError):
                wt.remove(checkout)
        cleanup.assert_not_called()

    def test_merged_checkout_can_be_removed_and_branch_is_retained(self) -> None:
        checkout = self.checkout()
        with patch.object(wt, "cleanup") as cleanup:
            wt.remove(checkout)
        cleanup.assert_called_once()
        self.assertFalse(checkout.root.exists())
        self.git(self.main, "rev-parse", "--verify", "feature")

    def test_test_runner_matches_unhosted_ci_without_loading_dotenv(self) -> None:
        checkout = self.checkout()
        state = wt.allocate(checkout)
        with patch.dict(os.environ, {"RAILWAY_ENVIRONMENT_NAME": "production"}):
            env = wt.runtime_env(state, testing=True)
        self.assertNotIn("RAILWAY_ENVIRONMENT_NAME", env)
        self.assertEqual(env["PYTHON_DOTENV_DISABLED"], "1")
        self.assertTrue(env["DATABASE_URL"].endswith(state["test_database"]))
        self.assertFalse(env["DATABASE_URL"].endswith(state["database"]))

    def test_database_environment_ignores_inherited_remote_connection(self) -> None:
        with patch.dict(os.environ, {"PGHOST": "production.example", "PGSERVICE": "prod"}):
            env = wt.database_env("portfolio_wt_test")
        self.assertEqual(env["PGHOST"], "127.0.0.1")
        self.assertNotIn("PGSERVICE", env)
        self.assertEqual(env["PGDATABASE"], "portfolio_wt_test")


if __name__ == "__main__":
    unittest.main()
