#!/usr/bin/env python3
"""Explicit credential installation with private intent journals and readback.

No deployment is triggered. Existing intent files require reconciliation, never
automatic replay. Values travel through stdin or private files, not argv/output.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import tempfile
import uuid
from pathlib import Path

from credential_recovery import (
    Store,
    allowed,
    default_root,
    locked,
    now,
    primary_checkout,
    read_private,
    require,
    write_private,
)

PROJECT = "73eb837a-ba86-4899-992c-cefd0c22b91f"
ENVIRONMENTS = {
    "development": "488d2a2e-3067-4d7f-83b5-ea280a8ad249",
    "production": "50022d5c-db02-4a48-8b5c-864f04af5118",
}
SERVICES = {
    "fastapi": "02f59dad-8f7a-4726-93ee-2d66019a0847",
    "react-router": "12fac8b1-79cb-4501-aaf0-1240e6cc67d8",
    "sernia-mcp": "a53277f0-4ba9-4433-b2b3-d790db80e168",
}


def cli(arguments: list[str], value: str | None = None) -> dict:
    result = subprocess.run(
        ["railway", *arguments],
        input=value,
        capture_output=True,
        text=True,
        timeout=90,
        env=dict(
            os.environ,
            RAILWAY_CALLER="skill:use-railway@1.6.1",
            RAILWAY_AGENT_SESSION="portfolio-credential-delivery",
        ),
    )
    require(result.returncode == 0, "Railway command failed; output suppressed")
    data = json.loads(result.stdout)
    require(not data.get("errors"), "Railway request failed; response suppressed")
    return data


def install_railway(store: Store, environment: str, service: str, names: list[str]) -> int:
    require(environment in ENVIRONMENTS and service in SERVICES)
    # Reuse pair and environment checks before any network mutation.
    prepared = Path(store.prepare(environment, service, names))
    values = json.loads(read_private(prepared))["values"]
    target = {
        "projectId": PROJECT,
        "environmentId": ENVIRONMENTS[environment],
        "serviceId": SERVICES[service],
    }
    args = [
        "--project",
        PROJECT,
        "--environment",
        target["environmentId"],
        "--service",
        target["serviceId"],
        "--json",
    ]
    journal = store.directory / f"delivery-{environment}-{service}.json"
    with locked(store.directory):
        # Always observe the actual target, including after an interrupted mutation.
        before = cli(["variable", "list", *args])
        if journal.exists():
            previous = json.loads(read_private(journal))
            require(
                previous["target"] == target and previous["values"] == values,
                "Delivery selection changed; use a separate recovery cycle",
            )
            require(
                all(before.get(k) == v for k, v in values.items()),
                "Prior delivery is uncertain; reconcile before retrying",
            )
            previous.update(state="verified", verified_at=now())
            write_private(journal, json.dumps(previous), replace=True)
            return len(values)
        intent = {
            "time": now(),
            "state": "intent",
            "target": target,
            "values": values,
            "prior": {k: before.get(k) for k in values},
            "prepared": str(prepared),
        }
        write_private(journal, json.dumps(intent))
        document = "mutation($input:VariableCollectionUpsertInput!){variableCollectionUpsert(input:$input)}"
        payload = {"input": {**target, "variables": values, "replace": False, "skipDeploys": True}}
        result = cli(["api", document, "--variables", "@-", "--compact"], json.dumps(payload))
        require(
            result.get("data", {}).get("variableCollectionUpsert") is True,
            "Delivery response uncertain; reconcile before retrying",
        )
        after = cli(["variable", "list", *args])
        require(
            all(after.get(k) == v for k, v in values.items()),
            "Delivery readback mismatch; reconcile before retrying",
        )
        intent.update(state="verified", verified_at=now())
        write_private(journal, json.dumps(intent), replace=True)
    for name in names:
        store.record(environment, name, "delivery", "verified", journal)
    return len(values)


def merged_dotenv(original: str, values: dict[str, str], retired: set[str]) -> str:
    from dotenv.parser import parse_stream

    result = []
    for binding in parse_stream(io.StringIO(original)):
        require(not binding.error, "Existing dotenv parse failed; input suppressed")
        if binding.key not in values and binding.key not in retired:
            result.append(binding.original.string)
    prefix = "".join(result).rstrip("\n") + "\n"
    # Literal single quotes work with python-dotenv and preserve PEM newlines.
    return prefix + "".join(
        name + "='" + value.replace("\\", "\\\\").replace("'", "\\'") + "'\n"
        for name, value in sorted(values.items())
    )


def install_local(store: Store, service: str, names: list[str]) -> int:
    from dotenv import dotenv_values

    require(service in {"fastapi", "react-router"})
    staged = store.read()["values"]["local"]
    require(
        names and all(k in staged and allowed(store.registry[k], "local", service) for k in names)
    )
    # Enforce pairs even for a local installation.
    store.prepare("local", service, names)
    values = {k: staged[k] for k in names}
    target = primary_checkout() / (".env" if service == "fastapi" else "apps/web-react-router/.env")
    require(not target.is_symlink() and target.is_file())
    require(target.stat().st_uid == os.getuid() and target.stat().st_nlink == 1)
    ignored = subprocess.run(
        ["git", "-C", str(primary_checkout()), "check-ignore", "--quiet", str(target)]
    )
    require(ignored.returncode == 0, "Local dotenv must be ignored")
    retired = {
        "ADMIN_PASSWORD_HASH",
        "ADMIN_PASSWORD_SALT",
        "GOOGLE_DRIVE_PICKER_API_KEY",
        "VITE_GOOGLE_DRIVE_PICKER_API_KEY",
        "VITE_GOOGLE_CLIENT_ID",
        "NEXT_PUBLIC_GOOGLE_CLIENT_ID",
    }
    aliases = {
        "QUO_API_KEY_PORTFOLIO": {"OPEN_PHONE_API_KEY"},
        "QUO_WEBHOOK_SECRET": {"OPEN_PHONE_WEBHOOK_SECRET"},
        "CLERK_WEBHOOK_SECRET": {"DEV_CLERK_WEBHOOK_SECRET", "PROD_CLERK_WEBHOOK_SECRET"},
    }
    for name in values:
        retired.update(aliases.get(name, set()))
    with locked(store.directory):
        original = target.read_text()
        replacement = merged_dotenv(original, values, retired)
        parsed = dotenv_values(stream=io.StringIO(replacement), interpolate=False)
        require(all(parsed.get(k) == v for k, v in values.items()), "Local serialization mismatch")
        label = f"local-{service}-{uuid.uuid4().hex}"
        write_private(store.directory / f"{label}.backup.env", original)
        journal = store.directory / f"{label}.json"
        write_private(
            journal,
            json.dumps({"time": now(), "state": "intent", "target": str(target), "names": names}),
        )
        fd, temp = tempfile.mkstemp(prefix=".recovery-", dir=target.parent)
        try:
            with os.fdopen(fd, "w") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(replacement)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, target)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
        readback = dotenv_values(target, interpolate=False)
        require(all(readback.get(k) == v for k, v in values.items()), "Local readback mismatch")
        write_private(
            journal,
            json.dumps({"time": now(), "state": "verified", "target": str(target), "names": names}),
            replace=True,
        )
    for name in names:
        store.record("local", name, "delivery", "verified", journal)
    return len(values)


def main() -> int:
    try:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("destination", choices=["local", "railway"])
        parser.add_argument("--cycle", required=True)
        parser.add_argument("--environment", choices=["local", *ENVIRONMENTS], required=True)
        parser.add_argument("--service", choices=SERVICES, required=True)
        parser.add_argument("--names", nargs="+", required=True)
        args = parser.parse_args()
        store = Store(default_root(), args.cycle)
        if args.destination == "local":
            require(args.environment == "local")
            count = install_local(store, args.service, args.names)
        else:
            count = install_railway(store, args.environment, args.service, args.names)
        print(f"Installed and readback verified: {count} variables; runtime unverified")
        return 0
    except Exception:
        print("Delivery stopped; inspect private journal. Values and errors suppressed.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
