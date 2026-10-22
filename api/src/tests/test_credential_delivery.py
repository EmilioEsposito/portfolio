"""Offline tests for secret delivery boundaries and uncertain outcomes."""

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
import credential_delivery as delivery
from credential_recovery import RecoveryError, Store, read_private
from dotenv import dotenv_values


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name).resolve(), "test", create=True)
        self.store.capture("development", {"CLICKUP_API_KEY": "synthetic-secret"}, "test")

    def tearDown(self):
        self.temp.cleanup()

    def test_no_deploy_no_argv_secret_and_readback(self):
        calls = []

        def run(args, value=None):
            calls.append((args, value))
            if args[0] == "api":
                payload = json.loads(value)["input"]
                self.assertTrue(payload["skipDeploys"])
                self.assertFalse(payload["replace"])
                self.assertNotIn("synthetic-secret", " ".join(args))
                return {"data": {"variableCollectionUpsert": True}}
            return {} if len(calls) == 1 else {"CLICKUP_API_KEY": "synthetic-secret"}

        with patch.object(delivery, "cli", side_effect=run):
            self.assertEqual(
                delivery.install_railway(self.store, "development", "fastapi", ["CLICKUP_API_KEY"]),
                1,
            )
        journal = json.loads(
            read_private(self.store.directory / "delivery-development-fastapi.json")
        )
        self.assertEqual(journal["state"], "verified")

    def test_uncertain_mutation_is_not_replayed(self):
        with patch.object(delivery, "cli", side_effect=[{}, TimeoutError()]):
            with self.assertRaises(TimeoutError):
                delivery.install_railway(self.store, "development", "fastapi", ["CLICKUP_API_KEY"])
        with patch.object(delivery, "cli", return_value={}) as cli:
            with self.assertRaises(RecoveryError):
                delivery.install_railway(self.store, "development", "fastapi", ["CLICKUP_API_KEY"])
            self.assertEqual(cli.call_count, 1)
        with patch.object(
            delivery, "cli", return_value={"CLICKUP_API_KEY": "synthetic-secret"}
        ) as cli:
            self.assertEqual(
                delivery.install_railway(self.store, "development", "fastapi", ["CLICKUP_API_KEY"]),
                1,
            )
            self.assertEqual(cli.call_count, 1)

    def test_local_roundtrip_preserves_unrelated_and_retires_aliases(self):
        values = {"VAPID_PRIVATE_KEY": "line1\nline2\\'end", "SESSION_SECRET_KEY": "${UNCHANGED}"}
        result = delivery.merged_dotenv(
            "# keep\nUNRELATED='original'\nOLD='retired'\nSESSION_SECRET_KEY='old'\n",
            values,
            {"OLD"},
        )
        parsed = dotenv_values(stream=io.StringIO(result), interpolate=False)
        self.assertEqual(parsed, {"UNRELATED": "original", **values})
        self.assertIn("# keep", result)


if __name__ == "__main__":
    unittest.main()
