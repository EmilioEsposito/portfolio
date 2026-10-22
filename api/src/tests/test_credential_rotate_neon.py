"""Synthetic tests for non-idempotent Neon rotation and private capture."""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
import credential_rotate_neon as rotate
from credential_recovery import Store, read_private


class NeonRotationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        source = Store(self.root, "source", create=True)
        source.capture("local", {"CLICKUP_API_KEY": "synthetic-preserved-key"}, "test")
        self.argv = [
            "rotate",
            "--source-cycle",
            "source",
            "--cycle",
            "rotated",
            "--environment",
            "local",
        ]

    def tearDown(self):
        self.temp.cleanup()

    def invoke(self, mock):
        out = io.StringIO()
        with (
            patch.object(rotate, "default_root", return_value=self.root),
            patch.object(sys, "argv", self.argv),
            patch.object(rotate.subprocess, "run", side_effect=mock),
            contextlib.redirect_stdout(out),
        ):
            status = rotate.main()
        self.assertNotIn("synthetic-database-password", out.getvalue())
        return status

    def test_capture_exact_local_branch_without_secret_arguments_or_replay(self):
        calls = []

        def run(args, **kwargs):
            calls.append(args)
            self.assertNotIn("synthetic-database-password", " ".join(args))
            if "api" in args:
                self.assertIn("/branches/br-gentle-forest-af508one/", " ".join(args))
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps({"role": {"password": "synthetic-database-password"}}),
                )
            suffix = "-pooler" if "--pooled" in args else ""
            return SimpleNamespace(
                returncode=0,
                stdout=f"postgresql://neondb_owner:synthetic-database-password@ep-falling-wind-afivxatv{suffix}.c-2.us-west-2.aws.neon.tech/neondb",
            )

        self.assertEqual(self.invoke(run), 0)
        saved = Store(self.root, "rotated").read()["values"]["local"]
        self.assertEqual(saved["CLICKUP_API_KEY"], "synthetic-preserved-key")
        self.assertIn("synthetic-database-password", saved["DATABASE_URL"])
        self.assertEqual(self.invoke(run), 1)
        self.assertEqual(len(calls), 3)

    def test_uncertain_reset_blocks_another_attempt(self):
        calls = []

        def fail(args, **kwargs):
            calls.append(args)
            raise TimeoutError("synthetic-database-password")

        self.assertEqual(self.invoke(fail), 1)
        self.assertEqual(self.invoke(fail), 1)
        self.assertEqual(len(calls), 1)
        journal = json.loads(read_private(self.root / "rotated/neon-rotation.json"))
        self.assertEqual(journal["state"], "intent")


if __name__ == "__main__":
    unittest.main()
