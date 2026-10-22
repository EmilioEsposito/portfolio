"""Synthetic offline custody regressions; no app imports or real credentials."""

import base64
import contextlib
import importlib.util
import io
import json
import os
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "credential_recovery", Path(__file__).resolve().parents[3] / "scripts/credentials/recovery.py"
)
recovery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recovery)


class CredentialRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        # macOS /var is a symlink; resolve the test fixture, never production input.
        self.root = Path(self.temp.name).resolve()
        self.store = recovery.Store(self.root, "test-cycle", create=True)
        self.network = patch.object(
            socket, "socket", side_effect=AssertionError("network forbidden")
        )
        self.network.start()

    def tearDown(self):
        self.network.stop()
        self.temp.cleanup()

    def source(self, content, mode=0o600):
        path = self.store.directory / "inbox" / "test.env"
        path.write_text(content)
        path.chmod(mode)
        return path

    def cli(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            result = recovery.main(["--root", str(self.root), "--cycle", "test-cycle", *args])
        return result, output.getvalue()

    def test_generation_is_private_independent_and_retry_safe(self):
        self.assertEqual(self.store.generate("development"), 3)
        self.assertEqual(self.store.generate("production"), 2)
        before = self.store.read()
        self.assertEqual(self.store.generate("development"), 0)
        self.assertEqual(before, self.store.read())
        values = [v for env in before["values"].values() for v in env.values()]
        self.assertEqual(len(set(values)), 5)
        self.assertTrue(all(len(v) == 64 for v in values))
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.store.directory.stat().st_mode & 0o777, 0o700)
        status = self.cli("status")[1]
        for value in values:
            self.assertNotIn(value, status)
        self.assertTrue(all(row["states"]["delivery"] == "unknown" for row in self.store.status()))

    def test_literal_dotenv_no_execution_or_interpolation(self):
        parsed = recovery.parse_env("""# comment
export CLICKUP_API_KEY='literal-$(touch /tmp/never)-${HOME}-`id`'
GOOGLE_OAUTH_CLIENT_SECRET="a=b#c" # comment
VAPID_PRIVATE_KEY='line 1
line 2'
LOGFIRE_TOKEN="first\\nsecond"
""")
        self.assertIn("${HOME}", parsed["CLICKUP_API_KEY"])
        self.assertEqual(parsed["GOOGLE_OAUTH_CLIENT_SECRET"], "a=b#c")
        self.assertEqual(parsed["VAPID_PRIVATE_KEY"], "line 1\nline 2")
        self.assertEqual(parsed["LOGFIRE_TOKEN"], "first\nsecond")
        self.assertEqual(
            recovery.parse_env('VAPID_PRIVATE_KEY="line1\nline2"')["VAPID_PRIVATE_KEY"],
            "line1\nline2",
        )

    def test_bad_input_never_echoes_values_or_changes_store(self):
        sentinel = "synthetic-SENSITIVE-do-not-echo"
        before = self.store.read()
        for text in (
            f"UNKNOWN={sentinel}",
            f"{sentinel}",
            f"CLICKUP_API_KEY={sentinel}\nCLICKUP_API_KEY=duplicate",
            f"CLICKUP_API_KEY='{sentinel}",
            "CLICKUP_API_KEY=****",
            f"ADMIN_PASSWORD_HASH={sentinel}",
        ):
            path = self.source(text)
            result, output = self.cli(
                "import-env", "--environment", "development", "--source", str(path)
            )
            self.assertEqual(result, 1)
            self.assertNotIn(sentinel, output)
            self.assertEqual(before, self.store.read())
        result, output = self.cli("--unknown", sentinel)
        self.assertEqual(result, 1)
        self.assertNotIn(sentinel, output)

    def test_permissions_symlinks_hardlinks_and_checkout_rejected(self):
        path = self.source("CLICKUP_API_KEY=synthetic-value", 0o644)
        with self.assertRaises(recovery.RecoveryError):
            recovery.read_private(path)
        path.chmod(0o600)
        link = path.with_name("link.env")
        link.symlink_to(path)
        with self.assertRaises(recovery.RecoveryError):
            recovery.read_private(link)
        hard = path.with_name("hard.env")
        os.link(path, hard)
        with self.assertRaises(recovery.RecoveryError):
            recovery.read_private(path)
        hard.unlink()
        with self.assertRaises(recovery.RecoveryError):
            recovery.Store(recovery.REPO / ".local", "never-create", create=True)
        alternate = self.root / "other-repo"
        alternate.mkdir(mode=0o700)
        (alternate / ".git").touch()
        with self.assertRaises(recovery.RecoveryError):
            recovery.Store(alternate, "never-create", create=True)
        linked_root = self.root / "linked"
        linked_root.symlink_to(self.store.directory, target_is_directory=True)
        with self.assertRaises(recovery.RecoveryError):
            recovery.private_dir(linked_root / "inbox")

    def test_capture_is_atomic_and_conflicts_fail(self):
        self.store.capture("development", {"CLICKUP_API_KEY": "synthetic-one"}, "fixture")
        before = self.store.read()
        self.assertEqual(
            self.store.capture("development", {"CLICKUP_API_KEY": "synthetic-one"}, "fixture"), 0
        )
        with self.assertRaises(recovery.RecoveryError):
            self.store.capture(
                "development",
                {"CLICKUP_API_KEY": "synthetic-two", "LOGFIRE_TOKEN": "synthetic-new"},
                "fixture",
            )
        self.assertEqual(before, self.store.read())

    def test_wrong_environment_and_backend_to_web_rejected(self):
        with self.assertRaises(recovery.RecoveryError):
            self.store.capture("production", {"CLERK_SECRET_KEY": "sk_test_synthetic"}, "fixture")
        self.store.generate("production")
        with self.assertRaises(recovery.RecoveryError):
            self.store.prepare("production", "react-router", ["CRON_SECRET"])
        with self.assertRaises(recovery.RecoveryError):
            self.store.prepare("production", "sernia-mcp", ["CRON_SECRET"])

    def test_preparation_is_targeted_immutable_and_not_activation(self):
        self.store.generate("production")
        first = self.store.prepare("production", "fastapi", ["CRON_SECRET"])
        second = self.store.prepare("production", "fastapi", ["CRON_SECRET"])
        self.assertNotEqual(first, second)
        payload = json.loads(recovery.read_private(Path(first)))
        self.assertEqual(set(payload["values"]), {"CRON_SECRET"})
        self.assertFalse(payload["deployment_authorized"])
        self.assertEqual(payload["environment"], "production")
        self.assertEqual(payload["service"], "fastapi")
        self.assertEqual(
            recovery.parse_env(recovery.read_private(Path(first).with_suffix(".env"))),
            payload["values"],
        )
        with self.assertRaises(recovery.RecoveryError):
            recovery.write_private(Path(first), "overwrite")
        self.assertTrue(all(r["states"]["delivery"] == "unknown" for r in self.store.status()))

    def test_logfire_rotation_reaches_mcp_but_not_web(self):
        for environment in ("development", "production"):
            self.store.capture(environment, {"LOGFIRE_TOKEN": "synthetic-logfire"}, "fixture")
            for service in ("fastapi", "sernia-mcp"):
                prepared = self.store.prepare(environment, service, ["LOGFIRE_TOKEN"])
                payload = json.loads(recovery.read_private(Path(prepared)))
                self.assertEqual(payload["values"], {"LOGFIRE_TOKEN": "synthetic-logfire"})
            with self.assertRaises(recovery.RecoveryError):
                self.store.prepare(environment, "react-router", ["LOGFIRE_TOKEN"])

    def test_production_mcp_oauth_payload_excludes_bearer_and_backend_admin_key(self):
        account = base64.b64encode(
            json.dumps(
                {
                    "type": "service_account",
                    "client_email": "synthetic@example.test",
                    "private_key": "synthetic-not-a-real-key",
                    "project_id": "synthetic-project",
                }
            ).encode()
        ).decode()
        required = {
            "FASTMCP_SERVER_AUTH_CLERK_DOMAIN": "clerk.example.test",
            "FASTMCP_SERVER_AUTH_CLERK_CLIENT_ID": "synthetic-client",
            "FASTMCP_SERVER_AUTH_CLERK_CLIENT_SECRET": "synthetic-client-secret",
            "SERNIA_MCP_BASE_URL": "https://mcp.example.test",
            "QUO_API_KEY_PORTFOLIO": "synthetic-quo",
            "CLICKUP_API_KEY": "synthetic-clickup",
            "GOOGLE_SERVICE_ACCOUNT_CREDENTIALS": account,
            "GITHUB_EMILIO_PERSONAL_WRITE_PAT": "synthetic-github",
            "LOGFIRE_TOKEN": "synthetic-logfire",
        }
        self.store.capture("production", required, "fixture")
        # Generation remains limited to the app's session/cron candidates.
        self.assertEqual(self.store.generate("production"), 2)
        prepared = self.store.prepare("production", "sernia-mcp", list(required))
        payload = json.loads(recovery.read_private(Path(prepared)))
        self.assertEqual(payload["values"], required)
        self.assertFalse(payload["deployment_authorized"])
        self.assertNotIn("SERNIA_MCP_INTERNAL_BEARER_TOKEN", payload["values"])
        # Historical captures stay readable, but cannot enable another prod auth path.
        self.store.capture(
            "production",
            {
                "SERNIA_MCP_INTERNAL_BEARER_TOKEN": "synthetic-bearer-" * 4,
                "CLERK_SECRET_KEY": "sk_live_synthetic",
            },
            "historical fixture",
        )
        for name in ("SERNIA_MCP_INTERNAL_BEARER_TOKEN", "CLERK_SECRET_KEY"):
            with self.assertRaises(recovery.RecoveryError):
                self.store.prepare("production", "sernia-mcp", [name])
            self.assertNotIn(name, self.store.plan("production", "sernia-mcp")["staged"])
        for name in (
            "FASTMCP_SERVER_AUTH_CLERK_CLIENT_SECRET",
            "SERNIA_MCP_BASE_URL",
        ):
            for service in ("fastapi", "react-router"):
                with self.assertRaises(recovery.RecoveryError):
                    self.store.prepare("production", service, [name])

    def test_database_pair_required_and_mismatch_rejected(self):
        self.store.capture(
            "development",
            {
                "DATABASE_URL": "postgresql://user:synthetic@ep-demo-pooler.example/db",
                "DATABASE_URL_UNPOOLED": "postgresql://user:synthetic@ep-demo.example/db",
            },
            "fixture",
        )
        with self.assertRaises(recovery.RecoveryError):
            self.store.prepare("development", "fastapi", ["DATABASE_URL"])
        self.store.prepare("development", "fastapi", ["DATABASE_URL", "DATABASE_URL_UNPOOLED"])
        self.store.capture(
            "production",
            {
                "DATABASE_URL": "postgresql://user:synthetic@ep-demo-pooler.example/db",
                "DATABASE_URL_UNPOOLED": "postgresql://user:other@ep-demo.example/db",
            },
            "fixture",
        )
        with self.assertRaises(recovery.RecoveryError):
            self.store.prepare("production", "fastapi", ["DATABASE_URL", "DATABASE_URL_UNPOOLED"])

    def test_vapid_pair_matching_and_mismatch(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        key = ec.generate_private_key(ec.SECP256R1())
        private = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
        public = (
            base64.urlsafe_b64encode(
                key.public_key().public_bytes(
                    serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
                )
            )
            .rstrip(b"=")
            .decode()
        )
        self.store.capture(
            "development", {"VAPID_PRIVATE_KEY": private, "VAPID_PUBLIC_KEY": public}, "fixture"
        )
        self.store.prepare("development", "fastapi", ["VAPID_PRIVATE_KEY", "VAPID_PUBLIC_KEY"])
        self.store.capture(
            "production",
            {"VAPID_PRIVATE_KEY": private, "VAPID_PUBLIC_KEY": "synthetic-mismatch"},
            "fixture",
        )
        with self.assertRaises(recovery.RecoveryError):
            self.store.prepare("production", "fastapi", ["VAPID_PRIVATE_KEY", "VAPID_PUBLIC_KEY"])

    def test_lock_contention_refuses_mutation(self):
        before = self.store.read()
        with recovery.locked(self.store.directory):
            result, _ = self.cli("generate", "--environment", "production")
        self.assertEqual(result, 1)
        self.assertEqual(before, self.store.read())

    def test_partial_write_preserves_previous_bundle(self):
        before = self.store.read()
        with patch.object(recovery.os, "replace", side_effect=OSError("synthetic secret in error")):
            result, output = self.cli("generate", "--environment", "production")
        self.assertEqual(result, 1)
        self.assertNotIn("synthetic secret", output)
        self.assertEqual(before, self.store.read())
        self.assertFalse(list(self.store.directory.glob(".capture-*")))

    def test_private_evidence_not_printed_and_stages_independent(self):
        evidence = self.source("synthetic-private-evidence-never-echo")
        self.store.record(
            "production", "CLERK_SECRET_KEY", "issuer_revocation", "reported", evidence
        )
        output = self.cli("status")[1]
        self.assertNotIn("synthetic-private-evidence", output)
        row = next(
            r
            for r in self.store.status()
            if r["environment"] == "production" and r["name"] == "CLERK_SECRET_KEY"
        )
        self.assertEqual(row["states"]["issuer_revocation"], "reported")
        self.assertEqual(row["states"]["provider_verification"], "unknown")
        self.assertFalse(row["staged"])
        with self.assertRaises(recovery.RecoveryError):
            self.store.record("production", "CLERK_SECRET_KEY", "capture", "verified", evidence)

    def test_new_cycle_does_not_copy_secrets_and_init_preserves_inbox(self):
        path = self.store.directory / "inbox/development.env"
        path.write_text("CLICKUP_API_KEY=synthetic-private")
        recovery.Store(self.root, "test-cycle", create=True)
        self.assertEqual(path.read_text(), "CLICKUP_API_KEY=synthetic-private")
        self.store.generate("production")
        second = recovery.Store(self.root, "second-cycle", create=True)
        self.assertEqual(second.read()["values"]["production"], {})

    def test_primary_store_requires_ignore_and_rejects_tracked_files(self):
        main = self.root / "primary"
        main.mkdir(mode=0o700)
        subprocess.run(["git", "init", str(main)], capture_output=True, check=True)
        with patch.object(recovery, "REPO", main):
            root = recovery.default_root()
            self.assertEqual(root, main / ".local/credential-recovery")
            with self.assertRaises(recovery.RecoveryError):
                recovery.Store(root, "blocked", create=True)
            (main / ".gitignore").write_text("/.local/credential-recovery/\n")
            store = recovery.Store(root, "allowed", create=True)
            store.generate("development")
            # A synthetic inbox is enough to prove gitignore cannot hide tracked files.
            subprocess.run(
                ["git", "-C", str(main), "add", "-f", str(store.directory / "inbox/local.env")],
                capture_output=True,
                check=True,
            )
            with self.assertRaises(recovery.RecoveryError):
                store.read()

    def test_linked_checkout_resolves_same_primary_store(self):
        main = self.root / "primary"
        main.mkdir(mode=0o700)
        subprocess.run(["git", "init", str(main)], capture_output=True, check=True)
        (main / ".gitignore").write_text("/.local/credential-recovery/\n")
        subprocess.run(
            ["git", "-C", str(main), "add", ".gitignore"], capture_output=True, check=True
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(main),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "commit",
                "--no-gpg-sign",
                "-m",
                "fixture",
            ],
            capture_output=True,
            check=True,
        )
        linked = self.root / "linked-checkout"
        subprocess.run(
            ["git", "-C", str(main), "worktree", "add", "--detach", str(linked)],
            capture_output=True,
            check=True,
        )
        with patch.object(recovery, "REPO", linked):
            self.assertEqual(recovery.default_root(), main / ".local/credential-recovery")
            recovery.Store(recovery.default_root(), "shared", create=True)
            with self.assertRaises(recovery.RecoveryError):
                recovery.Store(linked / ".local/credential-recovery", "wrong", create=True)


if __name__ == "__main__":
    unittest.main()
