#!/usr/bin/env python3
"""Mint one Google key into private custody; does not deliver it.

Requires the app account to be disabled and broad project roles already removed.
Explicit --restore-account enables the disabled account before creating the key.
An existing intent is never replayed. Reconcile IAM inventory after interruption.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess

# Direct-file execution must also resolve the reusable custody package.
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from credentials.recovery import (
    Store,
    default_root,
    locked,
    now,
    read_private,
    require,
    write_private,
)

PROJECT = "portfolio-450200"
ACCOUNT = "portfolio-app-service-account@portfolio-450200.iam.gserviceaccount.com"


def gcloud(args: list[str]) -> dict | list:
    result = subprocess.run(
        ["gcloud", *args, "--project", PROJECT, "--quiet", "--format=json"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    require(result.returncode == 0, "Google operation failed; output suppressed")
    return json.loads(result.stdout or "{}")


def mint(store: Store, restore_account: bool = False) -> None:
    require(restore_account, "Explicit account restoration required before minting")
    with locked(store.directory):
        account = gcloud(["iam", "service-accounts", "describe", ACCOUNT])
        require(account.get("disabled") is True and account.get("email") == ACCOUNT)
        policy = gcloud(["projects", "get-iam-policy", PROJECT])
        require(
            not any(
                b["role"] in {"roles/editor", "roles/owner", "roles/iam.serviceAccountTokenCreator"}
                and "serviceAccount:" + ACCOUNT in b.get("members", [])
                for b in policy.get("bindings", [])
            ),
            "Remove excessive project roles before minting",
        )
        journal = store.directory / "google-key-mint.json"
        keyfile = store.directory / "google-service-account.json"
        require(
            not journal.exists() and not keyfile.exists(), "Prior mint attempt exists; reconcile"
        )
        before = gcloud(
            [
                "iam",
                "service-accounts",
                "keys",
                "list",
                "--iam-account",
                ACCOUNT,
                "--managed-by=user",
            ]
        )
        write_private(
            journal,
            json.dumps(
                {"state": "intent", "time": now(), "account": ACCOUNT, "prior_keys": before}
            ),
        )
        gcloud(["iam", "service-accounts", "enable", ACCOUNT])
        previous_umask = os.umask(0o077)
        try:
            gcloud(
                [
                    "iam",
                    "service-accounts",
                    "keys",
                    "create",
                    str(keyfile),
                    "--iam-account",
                    ACCOUNT,
                ]
            )
        finally:
            os.umask(previous_umask)
        key = json.loads(read_private(keyfile))
        require(key["client_email"] == ACCOUNT and key["project_id"] == PROJECT)
        after = gcloud(
            [
                "iam",
                "service-accounts",
                "keys",
                "list",
                "--iam-account",
                ACCOUNT,
                "--managed-by=user",
            ]
        )
        require(any(k["name"].endswith("/" + key["private_key_id"]) for k in after))
        write_private(
            journal,
            json.dumps(
                {
                    "state": "captured",
                    "time": now(),
                    "account": ACCOUNT,
                    "key_id": key["private_key_id"],
                    "account_restored": True,
                }
            ),
            replace=True,
        )
    encoded = base64.b64encode(json.dumps(key).encode()).decode()
    for environment in ("development", "production", "local"):
        store.capture(environment, {"GOOGLE_SERVICE_ACCOUNT_CREDENTIALS": encoded}, str(journal))


def main() -> int:
    try:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--cycle", required=True)
        parser.add_argument("--restore-account", action="store_true")
        args = parser.parse_args()
        mint(Store(default_root(), args.cycle), args.restore_account)
        print("Google account restored; key captured and inventoried; no delivery")
        return 0
    except Exception:
        print("Mint stopped; inspect private journal and IAM inventory before retrying")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
