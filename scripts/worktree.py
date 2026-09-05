"""Local worktree lifecycle; shared by shell entry points and agent setup hooks.

Resource ownership lives in Git's private worktree metadata, never in the checkout.
Only the repository's local Docker Postgres is touched. See docs/WORKTREES.md.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import fcntl
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

STATE_FILE = "portfolio-worktree.json"
WEB_DIR = Path("apps/web-react-router")
LOCAL_URL = "postgresql://portfolio:portfolio@127.0.0.1:5432/"


class WorktreeError(Exception):
    """An actionable lifecycle error whose message contains no credentials."""


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    if result.returncode:
        raise WorktreeError(f"Git operation failed: {' '.join(args[:2])}")
    return result.stdout.strip()


@dataclass(frozen=True)
class Checkout:
    root: Path
    git_dir: Path
    common: Path
    main: Path

    @classmethod
    def find(cls, path: Path) -> Checkout:
        root = Path(git(path, "rev-parse", "--show-toplevel")).resolve()
        common = Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir"))
        git_dir = Path(git(root, "rev-parse", "--absolute-git-dir"))
        main = registered_worktrees(root)[0]
        return cls(root, git_dir, common, main)

    def require_linked(self) -> None:
        if self.git_dir == self.common or self.root not in registered_worktrees(self.main):
            raise WorktreeError("Expected a registered linked worktree, not the main checkout.")

    @property
    def state_path(self) -> Path:
        return self.git_dir / STATE_FILE


def registered_worktrees(root: Path) -> list[Path]:
    output = git(root, "worktree", "list", "--porcelain", "-z")
    return [Path(row[9:]).resolve() for row in output.split("\0") if row.startswith("worktree ")]


def identity(checkout: Checkout) -> str:
    readable = re.sub(r"[^a-z0-9]+", "-", checkout.root.name.lower()).strip("-")[:28]
    fingerprint = hashlib.sha256(str(checkout.git_dir).encode()).hexdigest()[:10]
    return f"{readable or 'worktree'}-{fingerprint}"


def read_state(checkout: Checkout) -> dict:
    if not checkout.state_path.exists():
        return {}
    state = json.loads(checkout.state_path.read_text())
    slug = state.get("slug", "")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,48}", slug):
        raise WorktreeError("Invalid recorded worktree identity; refusing resource operations.")
    suffix = slug.replace("-", "_")
    if (
        state.get("database") != f"portfolio_wt_{suffix}"
        or state.get("test_database") != f"portfolio_test_wt_{suffix}"
    ):
        raise WorktreeError("Recorded database ownership is invalid.")
    if state.get("owner") != hashlib.sha256(str(checkout.git_dir).encode()).hexdigest():
        raise WorktreeError("Worktree ownership metadata does not match this checkout.")
    for key in ("api_port", "web_port"):
        if type(state.get(key)) is not int or not 1024 < state[key] < 65536:
            raise WorktreeError("Invalid recorded port allocation.")
    return state


def save_state(checkout: Checkout, state: dict) -> None:
    with tempfile.NamedTemporaryFile(mode="w", dir=checkout.git_dir, delete=False) as file:
        json.dump(state, file, indent=2)
        file.write("\n")
        temporary = Path(file.name)
    temporary.replace(checkout.state_path)


@contextlib.contextmanager
def lock(path: Path) -> Iterator[None]:
    with path.open("a") as file:
        deadline = time.monotonic() + 30
        while True:
            try:
                fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise WorktreeError("Another worktree operation is running; retry shortly.")
                time.sleep(0.1)
        try:
            yield
        finally:
            fcntl.flock(file, fcntl.LOCK_UN)


def env_settings(path: Path) -> dict[str, str]:
    """Read simple managed settings only; never evaluate shell or interpolate secrets."""
    if not path.exists():
        return {}
    result = {}
    for line in path.read_text().splitlines():
        match = re.match(r"^(?:export\s+)?([A-Z_]+)\s*=\s*(.*)$", line)
        if match:
            result[match[1]] = match[2].strip().strip("\"'")
    return result


def update_env(source: Path, destination: Path, values: dict[str, str]) -> None:
    if destination.is_symlink():
        raise WorktreeError(f"Refusing to rewrite a symlinked environment file: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    content = (
        destination.read_text()
        if destination.exists()
        else (source.read_text() if source.exists() else "")
    )
    keys = "|".join(re.escape(key) for key in values)
    content = "\n".join(
        line
        for line in content.splitlines()
        if not re.match(rf"^(?:export\s+)?(?:{keys})\s*=", line)
    )
    with tempfile.NamedTemporaryFile(mode="w", dir=destination.parent, delete=False) as file:
        file.write(content.rstrip() + "\n\n")
        file.writelines(f"{key}={value}\n" for key, value in values.items())
        temporary = Path(file.name)
    temporary.replace(destination)


def port_available(port: int) -> bool:
    for family, host in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        try:
            with socket.socket(family) as sock:
                sock.bind((host, port))
        except OSError:
            return False
    return True


def allocate(checkout: Checkout) -> dict:
    with lock(checkout.common / "portfolio-port-allocation.lock"):
        state = read_state(checkout)
        if state:
            return state
        used = {8000, 5173}
        for path in registered_worktrees(checkout.main):
            if path == checkout.root or not path.exists():
                continue
            other = Checkout.find(path)
            recorded = read_state(other)
            used.update(recorded[key] for key in ("api_port", "web_port") if key in recorded)
            for env_path, keys in (
                (path / ".env", ("PORT",)),
                (path / WEB_DIR / ".env", ("VITE_PORT", "BACKEND_PORT")),
            ):
                settings = env_settings(env_path)
                used.update(int(settings[key]) for key in keys if settings.get(key, "").isdigit())
        for slot in range(1, 100):
            api_port, web_port = 8000 + 10 * slot, 5173 + 10 * slot
            if {api_port, web_port} & used or not all(map(port_available, (api_port, web_port))):
                continue
            slug = identity(checkout)
            suffix = slug.replace("-", "_")
            state = dict(
                slug=slug,
                owner=hashlib.sha256(str(checkout.git_dir).encode()).hexdigest(),
                database=f"portfolio_wt_{suffix}",
                test_database=f"portfolio_test_wt_{suffix}",
                api_port=api_port,
                web_port=web_port,
                provisioned=False,
            )
            save_state(checkout, state)
            return state
    raise WorktreeError("No free worktree port pair is available.")


def database_env(database: str) -> dict[str, str]:
    # libpq gives PGHOSTADDR/PGSERVICE precedence over PGHOST. Remove inherited
    # connection options altogether rather than letting a shell target a remote server.
    env = {key: value for key, value in os.environ.items() if not key.startswith("PG")}
    return dict(
        env,
        PGHOST="127.0.0.1",
        PGPORT="5432",
        PGUSER="portfolio",
        PGPASSWORD="portfolio",
        PGDATABASE=database,
        PGSSLMODE="disable",
        PGCONNECT_TIMEOUT="5",
    )


def sql(query: str, database: str = "postgres") -> str:
    result = subprocess.run(
        ["psql", "-X", "-qAt", "-v", "ON_ERROR_STOP=1", "-c", query],
        env=database_env(database),
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise WorktreeError(
            "Local PostgreSQL operation failed. Check Docker and the portfolio role."
        )
    return result.stdout.strip()


def ensure_postgres(checkout: Checkout) -> None:
    try:
        sql("SELECT 1")
    except WorktreeError:
        result = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                str(checkout.main / "docker-compose.yml"),
                "up",
                "-d",
                "postgres",
            ],
            cwd=checkout.main,
            capture_output=True,
        )
        if result.returncode:
            raise WorktreeError("Could not start local Postgres. Start Docker Desktop and retry.")
        for _ in range(20):
            try:
                sql("SELECT 1")
                return
            except WorktreeError:
                time.sleep(1)
        raise WorktreeError("Local Postgres did not become ready.")


def run_logged(
    checkout: Checkout, command: list[str], *, env: dict[str, str] | None = None
) -> None:
    log_path = checkout.git_dir / "portfolio-setup.log"
    descriptor = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(descriptor, "a") as log:
        result = subprocess.run(command, cwd=checkout.root, env=env, stdout=log, stderr=log)
    if result.returncode:
        raise WorktreeError(
            f"{command[0]} failed; setup remains retryable. Private log: {log_path}"
        )


def runtime_env(state: dict, *, testing: bool = False) -> dict[str, str]:
    database = state["test_database" if testing else "database"]
    env = dict(
        os.environ,
        DATABASE_URL=LOCAL_URL + database,
        DATABASE_URL_UNPOOLED=LOCAL_URL + database,
        DATABASE_REQUIRE_SSL="false",
        RAILWAY_ENVIRONMENT_NAME="",
        PORT=str(state["api_port"]),
    )
    if testing:
        # Match credential-free CI: app code distinguishes an absent variable from an empty one.
        env.pop("RAILWAY_ENVIRONMENT_NAME", None)
        env.update(
            PYTHON_DOTENV_DISABLED="1",
            OPENAI_API_KEY="test-openai-key",
            SERNIA_ANTHROPIC_API_KEY="test-anthropic-key",
            OPENROUTER_API_KEY="test-openrouter-key",
            LOGFIRE_SEND_TO_LOGFIRE="false",
        )
    return env


def owns_database(state: dict, database: str) -> bool:
    expected = "portfolio-worktree:" + state["owner"]
    return (
        sql(
            f"SELECT shobj_description(oid, 'pg_database') FROM pg_database WHERE datname='{database}'"
        )
        == expected
    )


def source_schema_compatible(checkout: Checkout) -> bool:
    if not sql("SELECT to_regclass('public.alembic_version')", "portfolio"):
        return False
    revisions = sql("SELECT version_num FROM alembic_version", "portfolio").splitlines()
    known = set()
    for path in (checkout.root / "api/src/database/migrations/versions").glob("*.py"):
        for node in ast.parse(path.read_text()).body:
            targets = (
                node.targets
                if isinstance(node, ast.Assign)
                else [node.target]
                if isinstance(node, ast.AnnAssign)
                else []
            )
            if any(isinstance(target, ast.Name) and target.id == "revision" for target in targets):
                if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    known.add(node.value.value)
    return bool(revisions) and all(revision in known for revision in revisions)


def prepare_database(checkout: Checkout, state: dict, *, testing: bool = False) -> None:
    database = state["test_database" if testing else "database"]
    exists = sql(f"SELECT 1 FROM pg_database WHERE datname='{database}'") == "1"
    if exists and not owns_database(state, database):
        raise WorktreeError(
            f"Database {database} exists without matching ownership; refusing to reuse it."
        )
    if not exists:
        state.pop(database + "_initialized", None)
        sql(f'CREATE DATABASE "{database}"')
        sql(f"COMMENT ON DATABASE \"{database}\" IS 'portfolio-worktree:{state['owner']}'")
    if not state.get(database + "_initialized"):
        source_exists = (
            not testing and sql("SELECT 1 FROM pg_database WHERE datname='portfolio'") == "1"
        )
        compatible = source_exists and source_schema_compatible(checkout)
        if source_exists and not compatible:
            print(
                "Local source schema differs from this branch; using a fresh seeded database.",
                flush=True,
            )
        if compatible:
            with tempfile.TemporaryFile() as dump:
                result = subprocess.run(
                    ["pg_dump", "--format=custom", "--no-owner", "--no-acl"],
                    env=database_env("portfolio"),
                    stdout=dump,
                    stderr=subprocess.DEVNULL,
                )
                if result.returncode:
                    raise WorktreeError(
                        "Local database snapshot failed; source data was not changed."
                    )
                dump.seek(0)
                result = subprocess.run(
                    [
                        "pg_restore",
                        "--dbname=" + database,
                        "--no-owner",
                        "--no-acl",
                        "--exit-on-error",
                        "--single-transaction",
                    ],
                    env=database_env(database),
                    stdin=dump,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                if result.returncode:
                    raise WorktreeError(
                        "Local snapshot restore failed; retry setup after checking PostgreSQL versions."
                    )
        state[database + "_initialized"] = True
        save_state(checkout, state)
    env = runtime_env(state, testing=testing)
    run_logged(checkout, ["uv", "run", "--frozen", "alembic", "upgrade", "head"], env=env)
    run_logged(checkout, ["uv", "run", "--frozen", "python", "api/seed_db.py"], env=env)


def provision(path: Path, *, once: bool = False) -> None:
    if once and (os.getenv("CLAUDE_CODE_REMOTE") == "true" or os.getenv("CODEX_CLOUD") == "1"):
        return
    try:
        checkout = Checkout.find(path)
        checkout.require_linked()
    except WorktreeError:
        if once:
            return
        raise
    if once and any(part.startswith("wf_") for part in checkout.root.parts[-2:]):
        return
    with lock(checkout.git_dir / "portfolio-lifecycle.lock"):
        state = read_state(checkout)
        if once and state.get("provisioned"):
            return
        for executable in ("uv", "pnpm", "node", "psql", "pg_dump", "pg_restore"):
            if not shutil.which(executable):
                raise WorktreeError(f"Missing {executable}; see docs/WORKTREES.md prerequisites.")
        version = (
            subprocess.run(["node", "--version"], capture_output=True, text=True)
            .stdout.strip()
            .lstrip("v")
            .split(".")
        )
        if tuple(map(int, version[:2])) < (22, 13):
            raise WorktreeError(
                "Node 22.13+ is required by the pinned pnpm 11. Activate it and retry."
            )
        state = allocate(checkout)
        print(
            f"Provisioning {checkout.root}\nAPI: http://localhost:{state['api_port']}\nWeb: http://localhost:{state['web_port']}",
            flush=True,
        )
        update_env(
            checkout.main / ".env",
            checkout.root / ".env",
            {
                "PORT": str(state["api_port"]),
                "DATABASE_URL": LOCAL_URL + state["database"],
                "DATABASE_URL_UNPOOLED": LOCAL_URL + state["database"],
                "DATABASE_REQUIRE_SSL": "false",
                "RAILWAY_ENVIRONMENT_NAME": "",
            },
        )
        update_env(
            checkout.main / WEB_DIR / ".env",
            checkout.root / WEB_DIR / ".env",
            {"BACKEND_PORT": str(state["api_port"]), "VITE_PORT": str(state["web_port"])},
        )
        print("Installing Python and Node dependencies…", flush=True)
        run_logged(checkout, ["uv", "sync", "--frozen", "-p", "3.11"])
        run_logged(
            checkout, ["pnpm", "install", "--frozen-lockfile"], env=dict(os.environ, CI="true")
        )
        print("Preparing isolated local database…", flush=True)
        ensure_postgres(checkout)
        prepare_database(checkout, state)
        state["provisioned"] = True
        save_state(checkout, state)
        print("Ready. Start with pnpm dev-with-fastapi; tests: ./scripts/worktree-test.sh")


def default_base(checkout: Checkout) -> str:
    try:
        return git(
            checkout.main, "symbolic-ref", "--short", "refs/remotes/origin/HEAD"
        ).removeprefix("origin/")
    except WorktreeError:
        return "main"


def create(description: str, base: str | None) -> None:
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,46}[a-z0-9])?", description):
        raise WorktreeError(
            "Use a lowercase name of up to 48 letters, digits, and internal hyphens."
        )
    checkout = Checkout.find(Path.cwd())
    if base is None or base.startswith("origin/"):
        branch = base.removeprefix("origin/") if base else default_base(checkout)
        git(checkout.main, "fetch", "origin", f"refs/heads/{branch}:refs/remotes/origin/{branch}")
        base = f"origin/{branch}"
    start = git(checkout.main, "rev-parse", "--verify", base + "^{commit}")
    target = checkout.main / ".claude" / "worktrees" / description
    target.parent.mkdir(parents=True, exist_ok=True)
    git(
        checkout.main,
        "worktree",
        "add",
        "--no-track",
        "-b",
        "codex/" + description,
        str(target),
        start,
    )
    print(
        f"Created {target} from {base}. Setup can be retried with worktree-provision.sh.",
        flush=True,
    )
    provision(target)


def resolve_target(value: str) -> Checkout:
    source = Checkout.find(Path.cwd())
    paths = registered_worktrees(source.main)
    requested = Path(value).expanduser().resolve()
    matches = [
        path for path in paths if path == requested or path.name in (value, "portfolio-" + value)
    ]
    if len(matches) != 1:
        raise WorktreeError(
            "Specify an exact registered worktree path (the name is missing or ambiguous)."
        )
    checkout = Checkout.find(matches[0])
    checkout.require_linked()
    return checkout


def preflight_remove(checkout: Checkout) -> None:
    checkout.require_linked()
    if Path.cwd().resolve().is_relative_to(checkout.root):
        raise WorktreeError(
            "Run cleanup from another checkout; the current worktree cannot remove itself."
        )
    if git(checkout.root, "status", "--porcelain", "--untracked-files=all"):
        raise WorktreeError("Worktree has uncommitted or untracked files; refusing removal.")
    if (checkout.git_dir / "locked").exists():
        raise WorktreeError("Worktree is locked; refusing removal.")


def cleanup(checkout: Checkout, *, dry_run: bool = False) -> None:
    checkout.require_linked()
    state = read_state(checkout)
    if not state:
        print("No recorded resources; leaving any legacy databases untouched.")
        return
    if not dry_run and not all(port_available(state[key]) for key in ("api_port", "web_port")):
        raise WorktreeError("Worktree ports are in use. Stop its development servers first.")
    databases = [state[key] for key in ("database", "test_database")]
    # Preflight every database before dropping any, so a busy test DB preserves both.
    existing = []
    for database in databases:
        if dry_run:
            print(f"Would drop owned local database {database}")
            continue
        if sql(f"SELECT 1 FROM pg_database WHERE datname='{database}'") != "1":
            continue
        if not owns_database(state, database):
            raise WorktreeError(f"Database {database} ownership does not match; refusing cleanup.")
        if sql(f"SELECT 1 FROM pg_stat_activity WHERE datname='{database}' LIMIT 1"):
            raise WorktreeError(
                f"Database {database} is in use. Stop this worktree's servers first."
            )
        existing.append(database)
    if dry_run:
        return
    for database in existing:
        sql(f'DROP DATABASE "{database}"')
        print(f"Dropped {database}")
    for database in databases:
        state.pop(database + "_initialized", None)
    state["provisioned"] = False
    save_state(checkout, state)


def mark(commit: str) -> None:
    checkout = Checkout.find(Path.cwd())
    checkout.require_linked()
    with lock(checkout.git_dir / "portfolio-lifecycle.lock"):
        state = read_state(checkout)
        if not state:
            raise WorktreeError("Provision the worktree before marking it for cleanup.")
        if git(checkout.root, "status", "--porcelain", "--untracked-files=all"):
            raise WorktreeError("Commit or save outstanding work before marking for cleanup.")
        branch = default_base(checkout)
        git(checkout.main, "fetch", "origin", branch)
        merged = git(checkout.main, "rev-parse", "--verify", commit + "^{commit}")
        git(checkout.main, "merge-base", "--is-ancestor", merged, f"origin/{branch}")
        state["cleanup"] = {"merge_commit": merged, "head": git(checkout.root, "rev-parse", "HEAD")}
        save_state(checkout, state)
        print("Marked for cleanup; later commits or dirty files will prevent removal.")


def remove(checkout: Checkout, *, dry_run: bool = False) -> None:
    preflight_remove(checkout)
    with lock(checkout.git_dir / "portfolio-lifecycle.lock"):
        state = read_state(checkout)
        branch = default_base(checkout)
        git(checkout.main, "fetch", "origin", branch)
        head = git(checkout.root, "rev-parse", "HEAD")
        marker = state.get("cleanup", {})
        if marker:
            if marker.get("head") != head:
                raise WorktreeError(
                    "Worktree has new commits since it was marked; refusing removal."
                )
            git(
                checkout.main,
                "merge-base",
                "--is-ancestor",
                marker["merge_commit"],
                f"origin/{branch}",
            )
        else:
            git(checkout.main, "merge-base", "--is-ancestor", head, f"origin/{branch}")
        cleanup(checkout, dry_run=dry_run)
        if dry_run:
            print(f"Would remove {checkout.root}")
            return
        # Git is the only directory remover. Branches are retained, including squash-merged ones.
        git(checkout.main, "worktree", "remove", str(checkout.root))
        print(f"Removed {checkout.root}; branch retained.")


def run_tests(args: list[str]) -> int:
    checkout = Checkout.find(Path.cwd())
    checkout.require_linked()
    state = read_state(checkout)
    if not state.get("provisioned"):
        raise WorktreeError("Provision this worktree before running isolated tests.")
    with lock(checkout.git_dir / "portfolio-lifecycle.lock"):
        ensure_postgres(checkout)
        prepare_database(checkout, state, testing=True)
        return subprocess.run(
            ["uv", "run", "--frozen", "pytest", *args],
            cwd=checkout.root,
            env=runtime_env(state, testing=True),
        ).returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("create")
    add.add_argument("description")
    add.add_argument("base", nargs="?")
    setup = commands.add_parser("provision")
    setup.add_argument("path", nargs="?", type=Path, default=Path.cwd())
    setup.add_argument("--once", action="store_true")
    delete = commands.add_parser("remove")
    delete.add_argument("target", nargs="?")
    delete.add_argument("--mark", metavar="MERGE_COMMIT")
    delete.add_argument("--eligible", action="store_true")
    delete.add_argument("--dry-run", action="store_true")
    release = commands.add_parser("cleanup")
    release.add_argument("--codex", type=Path, required=True)
    release.add_argument("--dry-run", action="store_true")
    commands.add_parser("audit")
    commands.add_parser("test")
    args, remaining = parser.parse_known_args()
    if remaining and args.command != "test":
        parser.error("unrecognized arguments: " + " ".join(remaining))
    dry_run = getattr(args, "dry_run", False) or os.getenv("DRY_RUN") == "1"
    try:
        if args.command == "create":
            create(args.description, args.base)
        elif args.command == "provision":
            provision(args.path, once=args.once)
        elif args.command == "test":
            return run_tests(remaining)
        elif args.command == "cleanup":
            expected = os.getenv("CODEX_WORKTREE_PATH")
            if not expected or args.codex.resolve() != Path(expected).resolve():
                raise WorktreeError("Cleanup target must exactly match CODEX_WORKTREE_PATH.")
            checkout = Checkout.find(args.codex)
            if checkout.root != args.codex.resolve():
                raise WorktreeError("Cleanup requires the worktree root, not a subdirectory.")
            with lock(checkout.git_dir / "portfolio-lifecycle.lock"):
                cleanup(checkout, dry_run=dry_run)
        elif args.command == "remove" and args.mark:
            mark(args.mark)
        elif args.command == "remove" and args.target:
            remove(resolve_target(args.target), dry_run=dry_run)
        else:
            if args.command == "remove" and not args.eligible:
                parser.error("provide a worktree, --mark, or --eligible")
            root = Checkout.find(Path.cwd())
            for path in registered_worktrees(root.main)[1:]:
                if not path.exists():
                    continue
                checkout = Checkout.find(path)
                state = read_state(checkout)
                if args.command == "audit":
                    print(json.dumps({"path": str(path), **state}))
                elif state.get("cleanup"):
                    try:
                        remove(checkout, dry_run=dry_run)
                    except WorktreeError as error:
                        print(f"Hold {path}: {error}")
        return 0
    except (WorktreeError, FileNotFoundError, json.JSONDecodeError) as error:
        print(f"Worktree setup: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
