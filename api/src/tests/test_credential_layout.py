"""Credential custody works without historical adapters or real private files."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


class CredentialLayoutTests(unittest.TestCase):
    def run_python(self, arguments, *, cwd=ROOT, pythonpath=None):
        env = dict(os.environ, PYTHONPATH=str(pythonpath or ROOT / "scripts"))
        result = subprocess.run(
            [sys.executable, *arguments],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def test_custody_without_incident_tree_and_without_network(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            shutil.copytree(
                ROOT / "scripts/credentials",
                root / "scripts/credentials",
                ignore=shutil.ignore_patterns("__pycache__"),
            )
            code = """
import contextlib
import io
import socket
from pathlib import Path
from unittest.mock import patch
with patch.object(socket, 'socket', side_effect=AssertionError('Network forbidden')):
    from credentials import recovery
    store = recovery.Store(Path('private').resolve(), 'synthetic', create=True)
    store.capture('development', {'CLICKUP_API_KEY': 'synthetic-private-value'}, 'fixture')
    assert store.read()['values']['development']['CLICKUP_API_KEY'] == 'synthetic-private-value'
    assert store.directory.stat().st_mode & 0o777 == 0o700
    assert store.path.stat().st_mode & 0o777 == 0o600
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        assert recovery.main(['--root', str(store.directory.parent), '--cycle', 'synthetic', 'status']) == 0
    assert 'synthetic-private-value' not in output.getvalue()
    assert not Path('scripts/incidents').exists()
"""
            result = self.run_python(["-c", code], cwd=root, pythonpath=root / "scripts")
            self.assertEqual(result.stdout, "")

    def test_old_imports_are_the_same_modules_not_copies(self):
        code = """
import importlib
import credential_recovery
from credentials import recovery
assert credential_recovery is recovery
for old, name in [('credential_delivery', 'delivery'), ('credential_mint_google', 'mint_google'), ('credential_rotate_neon', 'rotate_neon')]:
    legacy = importlib.import_module(old)
    canonical = importlib.import_module('incidents.2026-10-21.' + name)
    assert legacy is canonical
    assert legacy.Store is recovery.Store
"""
        self.assertEqual(self.run_python(["-c", code]).stdout, "")

    def test_both_command_paths_support_help_without_provider_access(self):
        for legacy, canonical in [
            ("credential_recovery.py", "credentials/recovery.py"),
            ("credential_delivery.py", "incidents/2026-10-21/delivery.py"),
            ("credential_mint_google.py", "incidents/2026-10-21/mint_google.py"),
            ("credential_rotate_neon.py", "incidents/2026-10-21/rotate_neon.py"),
        ]:
            for path in (legacy, canonical):
                with self.subTest(path=path):
                    result = self.run_python([str(ROOT / "scripts" / path), "--help"])
                    self.assertIn("usage:", result.stdout)

    def test_manifest_compatibility_has_one_source_of_truth(self):
        legacy = ROOT / "scripts/credential_recovery_manifest.json"
        self.assertTrue(legacy.is_symlink())
        self.assertEqual(legacy.resolve(), ROOT / "scripts/credentials/manifest.json")
