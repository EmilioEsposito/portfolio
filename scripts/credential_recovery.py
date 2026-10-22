#!/usr/bin/env python3
"""Offline credential custody. Never connects to a provider or installs configuration."""

from __future__ import annotations

import argparse
import base64
import contextlib
import datetime as dt
import fcntl
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / "scripts/credential_recovery_manifest.json"
ENVIRONMENTS = ("development", "production", "local")
STAGES = (
    "inventory",
    "issuer_revocation",
    "capture",
    "provider_verification",
    "delivery",
    "runtime_verification",
    "residual_access",
)
OUTCOMES = ("unknown", "reported", "verified", "failed", "uncertain", "held")
MAX_BYTES = 2_000_000


class RecoveryError(Exception):
    """Only static diagnostics; never interpolate input or provider content."""


def require(condition: bool, message: str = "Recovery validation failed") -> None:
    if not condition:
        raise RecoveryError(message)


def now() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


def primary_checkout() -> Path:
    """Resolve shared storage through Git metadata, never the current worktree."""
    result = subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", "--path-format=absolute", "--git-common-dir"],
        capture_output=True,
        text=True,
        check=True,
    )
    common = Path(result.stdout.strip())
    require(
        common.name == ".git" and common.is_dir() and not common.is_symlink(),
        "A normal primary checkout is required for shared recovery storage",
    )
    return common.parent


def default_root() -> Path:
    return primary_checkout() / ".local/credential-recovery"


def check_ignored_store(path: Path, main: Path) -> None:
    root = main / ".local/credential-recovery"
    require(path.is_relative_to(root), "Only the primary checkout recovery folder is allowed")
    # Ignore rules do not protect files already in the index (including force-added files).
    tracked = subprocess.run(
        ["git", "-C", str(main), "ls-files", "-z", "--", str(root)],
        capture_output=True,
        check=True,
    )
    require(not tracked.stdout, "Recovery folder contains tracked files; refusing access")
    for candidate in (root / ".ignore-probe", path):
        ignored = subprocess.run(
            ["git", "-C", str(main), "check-ignore", "--quiet", "--no-index", "--", str(candidate)],
            capture_output=True,
            check=False,
        )
        require(ignored.returncode == 0, "The entire recovery folder must be ignored")


def safe_path(path: Path) -> Path:
    path = Path(os.path.abspath(path.expanduser()))
    checkouts = []
    for part in (path, *path.parents):
        require(not part.is_symlink(), "Symlink paths are not allowed")
        if (part / ".git").exists():
            checkouts.append(part)
    if checkouts:
        main = primary_checkout()
        require(checkouts == [main], "Recovery files cannot live in a linked or other checkout")
        check_ignored_store(path, main)
    return path


def private_dir(path: Path, create: bool = False) -> Path:
    path = safe_path(path)
    if create and not path.exists():
        # mkdir(parents=True) does not apply the requested mode to intermediate dirs.
        missing = []
        parent = path
        while not parent.exists():
            missing.append(parent)
            parent = parent.parent
        for directory in reversed(missing):
            directory.mkdir(mode=0o700)
    meta = path.stat()
    require(
        stat.S_ISDIR(meta.st_mode)
        and meta.st_uid == os.getuid()
        and stat.S_IMODE(meta.st_mode) == 0o700,
        "Private directory must be owned by you with mode 0700",
    )
    return path


def check_file(fd: int) -> None:
    meta = os.fstat(fd)
    require(
        stat.S_ISREG(meta.st_mode)
        and meta.st_uid == os.getuid()
        and stat.S_IMODE(meta.st_mode) == 0o600
        and meta.st_nlink == 1,
        "Private file must be singly linked, owned by you, and mode 0600",
    )


def read_private(path: Path) -> str:
    path = safe_path(path)
    private_dir(path.parent)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "r", encoding="utf-8") as stream:
        check_file(stream.fileno())
        text = stream.read(MAX_BYTES + 1)
    require(len(text.encode()) <= MAX_BYTES, "Private input exceeds size limit")
    return text


def write_private(path: Path, text: str, replace: bool = False) -> None:
    path = safe_path(path)
    private_dir(path.parent)
    require(len(text.encode()) <= MAX_BYTES, "Private output exceeds size limit")
    if path.exists():
        read_private(path)
        require(replace, "Refusing to overwrite existing private file")
    fd, temp = tempfile.mkstemp(prefix=".capture-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            os.replace(temp, path)
        else:
            # Atomic no-clobber publication, unlike exists() + rename().
            os.link(temp, path)
            os.unlink(temp)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


@contextlib.contextmanager
def locked(directory: Path):
    fd = os.open(directory / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        check_file(fd)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def parse_env(text: str) -> dict[str, str]:
    """Strict literal dotenv subset; no shell execution or variable interpolation.

    Supports export NAME=, unquoted literals, JSON double-quoted strings, and
    literal single-quoted multiline values. Unknown/duplicate assignments fail.
    """
    result = {}
    lines = iter(text.splitlines())
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=\s*(.*)", line)
        require(match is not None, "Invalid dotenv assignment; input suppressed")
        name, raw = match.groups()
        require(name not in result, "Duplicate dotenv assignment; input suppressed")
        if raw.startswith("'"):
            while "'" not in raw[1:]:
                more = next(lines, None)
                require(more is not None, "Unterminated quoted value")
                raw += "\n" + more
            end = raw.index("'", 1)
            require(
                not raw[end + 1 :].strip() or raw[end + 1 :].lstrip().startswith("#"),
                "Unexpected text after quoted value",
            )
            value = raw[1:end]
        elif raw.startswith('"'):
            # JSON escaping is deterministic and exports round-trip through this parser.
            while True:
                try:
                    value, end = json.JSONDecoder(strict=False).raw_decode(raw)
                    break
                except ValueError:
                    more = next(lines, None)
                    require(more is not None, "Invalid double-quoted value")
                    raw += "\n" + more
            require(isinstance(value, str), "Expected quoted string")
            require(
                not raw[end:].strip() or raw[end:].lstrip().startswith("#"),
                "Unexpected text after quoted value",
            )
        else:
            value = re.split(r"\s+#", raw, maxsplit=1)[0].rstrip()
        require(value and "\x00" not in value, "Empty or invalid value")
        require(
            value.strip("* =")
            and value.lower() not in {"changeme", "replace_me", "todo"}
            and not value.startswith("<"),
            "Placeholder value rejected",
        )
        result[name] = value
    require(bool(result), "No assignments found")
    return result


def manifest() -> dict:
    data = json.loads(MANIFEST.read_text())
    require(data["schema_version"] == 1, "Unsupported manifest")
    return data["variables"]


def allowed(spec: dict, environment: str, service: str | None = None) -> bool:
    if service == "sernia-mcp" and environment != "development":
        return False  # No production/local MCP recovery target has been established.
    return environment in spec["environments"] and (service is None or service in spec["services"])


def validate_values(values: dict, environment: str, registry: dict) -> None:
    require(environment in ENVIRONMENTS)
    for name, value in values.items():
        require(
            name in registry and allowed(registry[name], environment),
            "Unknown, retired, or wrong-environment variable; input suppressed",
        )
        require(isinstance(value, str) and value and "\x00" not in value, "Invalid value")
        if registry[name].get("generate"):
            require(len(value) >= 32, "App-owned secrets require at least 32 characters")
        if name == "CLERK_SECRET_KEY":
            prefix = "sk_live_" if environment == "production" else "sk_test_"
            require(value.startswith(prefix), "Clerk key mode does not match environment")
        if name == "GOOGLE_SERVICE_ACCOUNT_CREDENTIALS":
            account = json.loads(base64.b64decode(value, validate=True))
            require(
                isinstance(account, dict)
                and account.get("type") == "service_account"
                and all(account.get(k) for k in ("client_email", "private_key", "project_id")),
                "Invalid service-account JSON",
            )


class Store:
    def __init__(self, root: Path, cycle: str, create: bool = False):
        require(re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", cycle) is not None, "Invalid cycle label")
        self.registry = manifest()
        self.directory = private_dir(root / cycle, create=create)
        self.path = self.directory / "bundle.json"
        if create:
            with locked(self.directory):
                if not self.path.exists():
                    write_private(
                        self.path,
                        json.dumps(
                            {
                                "schema_version": 1,
                                "cycle": cycle,
                                "values": {e: {} for e in ENVIRONMENTS},
                                "events": [],
                            },
                            indent=2,
                        ),
                    )
                for environment in ENVIRONMENTS:
                    inbox = self.directory / "inbox"
                    private_dir(inbox, create=True)
                    path = inbox / f"{environment}.env"
                    if not path.exists():
                        write_private(
                            path,
                            "# Paste replacements from your password manager here.\n"
                            "# Literal NAME=value assignments only. Never source this file.\n",
                        )
        self.read()

    def read(self) -> dict:
        data = json.loads(read_private(self.path))
        require(
            set(data) == {"schema_version", "cycle", "values", "events"}
            and data["schema_version"] == 1
            and data["cycle"] == self.directory.name
            and set(data["values"]) == set(ENVIRONMENTS)
            and isinstance(data["events"], list),
            "Invalid bundle schema",
        )
        for environment, values in data["values"].items():
            require(isinstance(values, dict), "Invalid bundle values")
            validate_values(values, environment, self.registry)
        for event in data["events"]:
            require(
                isinstance(event, dict)
                and event.get("name") in self.registry
                and event.get("environment") in ENVIRONMENTS
                and event.get("stage") in STAGES
                and event.get("outcome") in OUTCOMES,
                "Invalid ledger event",
            )
        return data

    def save(self, data: dict) -> None:
        write_private(self.path, json.dumps(data, indent=2) + "\n", replace=True)
        require(self.read() == data, "Private readback failed")

    def capture(self, environment: str, values: dict[str, str], evidence: str) -> int:
        validate_values(values, environment, self.registry)
        with locked(self.directory):
            data = self.read()
            current = data["values"][environment]
            require(
                all(
                    k not in current or secrets.compare_digest(current[k].encode(), v.encode())
                    for k, v in values.items()
                ),
                "Different value already staged; use a new cycle instead of overwriting",
            )
            additions = {k: v for k, v in values.items() if k not in current}
            current.update(additions)
            for name in additions:
                data["events"].append(
                    {
                        "name": name,
                        "environment": environment,
                        "stage": "capture",
                        "outcome": "verified",
                        "time": now(),
                        "evidence": evidence,
                        "meaning": "local custody only; provider unverified",
                    }
                )
            self.save(data)
            return len(additions)

    def generate(self, environment: str) -> int:
        # A retry reuses existing material. No generation of provider-issued credentials.
        with locked(self.directory):
            data = self.read()
            values = data["values"][environment]
            names = [
                name
                for name, spec in self.registry.items()
                if spec.get("generate")
                and allowed(spec, environment)
                and name not in values
                and environment in spec.get("generate_environments", ENVIRONMENTS)
            ]
            for name in names:
                values[name] = secrets.token_urlsafe(48)
                data["events"].append(
                    {
                        "name": name,
                        "environment": environment,
                        "stage": "capture",
                        "outcome": "verified",
                        "time": now(),
                        "evidence": "local CSPRNG generation and private readback",
                        "meaning": "candidate only; old secret not invalidated",
                    }
                )
            self.save(data)
            return len(names)

    def record(self, environment: str, name: str, stage: str, outcome: str, evidence: Path) -> None:
        require(name in self.registry and allowed(self.registry[name], environment))
        require(stage in STAGES and outcome in OUTCOMES)
        require(stage != "capture", "Capture state is recorded by capture commands")
        # Evidence is private; never echoed. These are operator assertions, not tool proofs.
        require(bool(read_private(evidence).strip()), "Evidence file must not be empty")
        with locked(self.directory):
            data = self.read()
            data["events"].append(
                {
                    "name": name,
                    "environment": environment,
                    "stage": stage,
                    "outcome": outcome,
                    "time": now(),
                    "evidence": str(evidence.absolute()),
                    "meaning": "operator assertion",
                }
            )
            self.save(data)

    def plan(self, environment: str, service: str) -> dict:
        staged = self.read()["values"][environment]
        names = sorted(
            k for k, spec in self.registry.items() if allowed(spec, environment, service)
        )
        require(bool(names), "Unsupported environment/service combination")
        return {
            "environment": environment,
            "service": service,
            "delivery": "held",
            "runtime": "unverified",
            "staged": [k for k in names if k in staged],
            "not_staged": [k for k in names if k not in staged],
            "note": "Inventory candidates, not all mandatory startup requirements",
        }

    def prepare(self, environment: str, service: str, names: list[str]) -> str:
        require(names and len(set(names)) == len(names), "Select distinct variable names")
        with locked(self.directory):
            data = self.read()
            for name in names:
                require(
                    name in self.registry and allowed(self.registry[name], environment, service),
                    "Variable not allowed for this target",
                )
                require(name in data["values"][environment], "Selected variable is not staged")
            selected = {k: data["values"][environment][k] for k in sorted(names)}
            # Related pairs must travel together, never silently pair new and stale values.
            for pair in (
                {"DATABASE_URL", "DATABASE_URL_UNPOOLED"},
                {"VAPID_PRIVATE_KEY", "VAPID_PUBLIC_KEY"},
            ):
                require(
                    not pair.intersection(names) or pair.issubset(names),
                    "Related credential pair must be prepared together",
                )
            if "DATABASE_URL" in selected:
                pooled = urlsplit(selected["DATABASE_URL"])
                direct = urlsplit(selected["DATABASE_URL_UNPOOLED"])
                require(
                    all(
                        u.scheme in {"postgres", "postgresql", "postgresql+asyncpg"}
                        and u.username
                        and u.password
                        and u.hostname
                        and u.path
                        for u in (pooled, direct)
                    )
                )
                require(
                    (pooled.username, pooled.password, pooled.path)
                    == (direct.username, direct.password, direct.path)
                    and pooled.hostname.replace("-pooler.", ".") == direct.hostname,
                    "Database pair does not identify the same role/database/endpoint",
                )
            if "VAPID_PRIVATE_KEY" in selected:
                import base64

                from cryptography.hazmat.primitives import serialization
                from cryptography.hazmat.primitives.asymmetric import ec

                key = serialization.load_pem_private_key(
                    selected["VAPID_PRIVATE_KEY"].encode(), password=None
                )
                require(
                    isinstance(key, ec.EllipticCurvePrivateKey)
                    and isinstance(key.curve, ec.SECP256R1)
                )
                public = key.public_key().public_bytes(
                    serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
                )
                expected = base64.urlsafe_b64encode(public).rstrip(b"=").decode()
                require(
                    secrets.compare_digest(expected, selected["VAPID_PUBLIC_KEY"]),
                    "VAPID public/private pair mismatch",
                )
            out = private_dir(self.directory / "prepared", create=True)
            label = f"{environment}-{service}-{uuid.uuid4().hex}"
            payload = {
                "environment": environment,
                "service": service,
                "deployment_authorized": False,
                "values": selected,
            }
            write_private(out / f"{label}.json", json.dumps(payload, indent=2) + "\n")
            require(
                json.loads(read_private(out / f"{label}.json")) == payload,
                "Prepared payload readback failed",
            )
            dotenv = "".join(f"{name}={json.dumps(value)}\n" for name, value in selected.items())
            write_private(out / f"{label}.env", dotenv)
            require(
                parse_env(read_private(out / f"{label}.env")) == selected,
                "Prepared dotenv readback failed",
            )
            # No activation ledger state is advanced by assembly.
            return str(out / f"{label}.json")

    def status(self) -> list[dict]:
        data = self.read()
        rows = []
        for environment in ENVIRONMENTS:
            for name, spec in self.registry.items():
                if not allowed(spec, environment):
                    continue
                states = {stage: "unknown" for stage in STAGES}
                for event in data["events"]:
                    if event["name"] == name and event["environment"] == environment:
                        states[event["stage"]] = event["outcome"]
                rows.append(
                    {
                        "environment": environment,
                        "name": name,
                        "staged": name in data["values"][environment],
                        "states": states,
                    }
                )
        return rows


class QuietParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise RecoveryError("Invalid command arguments; use --help (values suppressed)")


def main(arguments: list[str] | None = None) -> int:
    try:
        parser = QuietParser(description=__doc__)
        parser.add_argument("--root", type=Path)
        parser.add_argument("--cycle", required=True)
        parser.add_argument(
            "command",
            choices=("init", "import-env", "generate", "status", "plan", "prepare", "record"),
        )
        parser.add_argument("--environment", choices=ENVIRONMENTS)
        parser.add_argument(
            "--service", choices=("fastapi", "react-router", "sernia-mcp", "github-actions")
        )
        parser.add_argument("--source", type=Path)
        parser.add_argument("--names", nargs="+")
        parser.add_argument("--stage", choices=STAGES)
        parser.add_argument("--outcome", choices=OUTCOMES)
        args = parser.parse_args(arguments)
        store = Store(
            args.root if args.root is not None else default_root(),
            args.cycle,
            create=args.command == "init",
        )
        if args.command == "init":
            print("Private cycle and environment inboxes ready; existing files preserved")
        elif args.command == "status":
            print(json.dumps(store.status(), indent=2))
        else:
            require(args.environment in ENVIRONMENTS, "Select an environment")
            if args.command == "generate":
                count = store.generate(args.environment)
                print(f"App-owned candidates privately staged: {count}; no activation")
            elif args.command == "import-env":
                require(args.source is not None, "Select a private dotenv source")
                values = parse_env(read_private(args.source))
                count = store.capture(args.environment, values, "explicit private dotenv import")
                print(f"New assignments privately captured: {count}; provider identity unverified")
            elif args.command == "record":
                require(
                    args.names
                    and len(args.names) == 1
                    and args.source
                    and args.stage
                    and args.outcome,
                    "Select one variable, stage, outcome, and evidence file",
                )
                store.record(args.environment, args.names[0], args.stage, args.outcome, args.source)
                print("Private lifecycle assertion recorded; no provider action performed")
            else:
                require(args.service is not None, "Select a service")
                if args.command == "plan":
                    print(json.dumps(store.plan(args.environment, args.service), indent=2))
                else:
                    print(
                        "Private payload prepared; no upload: "
                        + store.prepare(args.environment, args.service, args.names)
                    )
        return 0
    except Exception:
        # Includes parser/OS/JSON errors: none may echo a value, line, path, or response.
        print(
            "Recovery operation failed closed; details suppressed. Check the documented input and file permissions.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
