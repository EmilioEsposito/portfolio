"""Offline checks for the bootstrap guard, independent of application credentials."""

import importlib.util
import os
import shutil
import ssl
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "python_trust", ROOT / "apps/sernia_mcp/trust/check_python_trust.py"
)
trust = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(trust)


class PythonTrustTests(unittest.TestCase):
    def test_imported_package_version_wins_over_stale_distribution_metadata(self) -> None:
        for imported, recorded, safe in (("26.2.2", "24.0", True), ("24.0", "26.2.2", False)):
            with self.subTest(imported=imported, recorded=recorded):
                package = ModuleType("pip")
                package.__version__ = imported
                with (
                    patch.object(trust.importlib, "import_module", return_value=package),
                    patch.object(trust, "version", return_value=recorded),
                ):
                    if safe:
                        self.assertIs(trust.check_package_version("pip", "26.2.2"), package)
                    else:
                        with self.assertRaisesRegex(ValueError, "before"):
                            trust.check_package_version("pip", "26.2.2")

    def test_old_installers_fail_independently_of_application_certifi(self) -> None:
        for name, old, minimum in (
            ("pip", "26.2.1", "26.2.2"),
            ("uv", "0.13.3", "0.13.4"),
            ("certifi", "2026.10.18", "2026.10.19"),
        ):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "before"):
                trust.require_version(name, old, minimum)
            trust.require_version(name, minimum, minimum)

    def test_empty_trust_store_fails(self) -> None:
        with self.assertRaisesRegex(ValueError, "No CA certificates"):
            trust.check_context(ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT), "empty")

    @unittest.skipUnless(shutil.which("openssl"), "openssl CLI required")
    def test_pem_bundle_containing_distrusted_subject_fails(self) -> None:
        # A synthetic CA tests the actual PEM loading path, without network access.
        with tempfile.TemporaryDirectory() as directory:
            certificate = Path(directory) / "ca.pem"
            subprocess.run(
                [
                    "openssl",
                    "req",
                    "-x509",
                    "-newkey",
                    "rsa:2048",
                    "-nodes",
                    "-keyout",
                    str(Path(directory) / "key.pem"),
                    "-out",
                    str(certificate),
                    "-days",
                    "1",
                    "-subj",
                    f"/CN={trust.BAD_ROOT}",
                    "-addext",
                    "basicConstraints=critical,CA:TRUE",
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            with self.assertRaisesRegex(ValueError, "Compromised root"):
                trust.check_bundle(str(certificate), "synthetic override")

    @unittest.skipUnless(shutil.which("openssl"), "openssl CLI required")
    def test_directory_only_openssl_store_is_loaded_before_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run(
                [
                    "openssl",
                    "req",
                    "-x509",
                    "-newkey",
                    "rsa:2048",
                    "-nodes",
                    "-keyout",
                    str(Path(directory) / "key.pem"),
                    "-out",
                    str(Path(directory) / "ca.pem"),
                    "-days",
                    "1",
                    "-subj",
                    "/CN=Safe synthetic test CA",
                    "-addext",
                    "basicConstraints=critical,CA:TRUE",
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            subject_hash = subprocess.check_output(
                ["openssl", "x509", "-hash", "-noout", "-in", str(Path(directory) / "ca.pem")],
                text=True,
            ).strip()
            (Path(directory) / f"{subject_hash}.0").symlink_to("ca.pem")
            with patch.dict(
                os.environ, {"SSL_CERT_FILE": "/nonexistent/ca.pem", "SSL_CERT_DIR": directory}
            ):
                self.assertEqual(ssl.create_default_context().get_ca_certs(), [])
                trust.check_default_trust()

    def test_missing_override_does_not_fall_back_to_system_roots(self) -> None:
        with self.assertRaises(OSError):
            trust.check_bundle("/nonexistent/portfolio-ca.pem", "missing override")
