#!/usr/bin/env python3
"""Explicit Neon role rotation with private capture and no automatic retries.

Use a new cycle. Only the selected environment is copied from --source-cycle;
its database URLs are replaced by fresh provider reads after password rotation.
This does not install configuration, deploy, or terminate existing sessions.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from urllib.parse import urlsplit

from credential_recovery import Store, default_root, locked, now, require, write_private

PROJECT = "sweet-butterfly-10388995"
BRANCHES = {
    "local": ("br-gentle-forest-af508one", "ep-falling-wind-afivxatv"),
    "development": ("br-purple-fog-afgqu99y", "ep-summer-art-afsu1zfz"),
    "production": ("br-small-forest-af9ve5ur", "ep-polished-lab-af9ghkns"),
}


def main() -> int:
    try:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--source-cycle", required=True)
        parser.add_argument("--cycle", required=True)
        parser.add_argument("--environment", choices=BRANCHES, required=True)
        args = parser.parse_args()
        require(args.source_cycle != args.cycle, "Select a fresh cycle")
        source = Store(default_root(), args.source_cycle)
        target = Store(default_root(), args.cycle, create=True)
        branch, endpoint = BRANCHES[args.environment]
        values = {
            k: v
            for k, v in source.read()["values"][args.environment].items()
            if k not in {"DATABASE_URL", "DATABASE_URL_UNPOOLED"}
        }
        journal = target.directory / "neon-rotation.json"
        with locked(target.directory):
            require(not journal.exists(), "Prior rotation exists; reconcile instead of replaying")
            write_private(
                journal,
                json.dumps(
                    {
                        "state": "intent",
                        "time": now(),
                        "branch": branch,
                        "project": PROJECT,
                        "role": "neondb_owner",
                    }
                ),
            )
            result = subprocess.run(
                [
                    "pnpm",
                    "dlx",
                    "neonctl",
                    "api",
                    f"/projects/{PROJECT}/branches/{branch}/roles/neondb_owner/reset_password",
                    "--method",
                    "POST",
                    "--output",
                    "json",
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
            require(result.returncode == 0, "Rotation uncertain; reconcile")
            response = json.loads(result.stdout)
            write_private(target.directory / "neon-reset-response.json", json.dumps(response))
            for name, pooled in (("DATABASE_URL", True), ("DATABASE_URL_UNPOOLED", False)):
                command = [
                    "pnpm",
                    "dlx",
                    "neonctl",
                    "connection-string",
                    branch,
                    "--project-id",
                    PROJECT,
                    "--role-name",
                    "neondb_owner",
                    "--database-name",
                    "neondb",
                    "--no-color",
                ]
                if pooled:
                    command.append("--pooled")
                result = subprocess.run(command, capture_output=True, text=True, timeout=45)
                require(result.returncode == 0, "Connection capture failed; do not repeat rotation")
                value = result.stdout.strip().strip("'\"")
                parsed = urlsplit(value)
                require(
                    parsed.scheme == "postgresql"
                    and parsed.password
                    and parsed.username == "neondb_owner"
                )
                require(parsed.hostname.replace("-pooler.", ".").split(".")[0] == endpoint)
                values[name] = value
            write_private(
                journal,
                json.dumps(
                    {
                        "state": "captured",
                        "time": now(),
                        "branch": branch,
                        "project": PROJECT,
                        "role": "neondb_owner",
                    }
                ),
                replace=True,
            )
        target.capture(args.environment, values, str(journal))
        print("Neon password rotated and captured privately; configuration delivery still required")
        return 0
    except Exception:
        print("Rotation stopped; reconcile provider and private journal before any retry")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
