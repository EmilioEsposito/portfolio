"""Keep api/src/portfolio/ the code that exists only for eesposito.com.

Every package directly under api/src/ is classified below, and api/README.md
lists the same classification. Portfolio code may import only itself and the
shared packages it uses today; outside portfolio/, only api/index.py and tests
import it.
"""

import ast
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "api" / "src"
PORTFOLIO_DIR = SRC / "portfolio"
TESTS_DIR = SRC / "tests"
APP = ROOT / "api" / "index.py"

PORTFOLIO = "portfolio"
SERNIA_OPERATIONS = frozenset(
    {
        "adhoc",
        "agent_conversations",
        "clickup",
        "contact",
        "google",
        "open_phone",
        "prefect",
        "rental_listings",
        "sernia_ai",
        "sernia_mcp_host",  # TRIAL, not for merge
        "showing_bookings",
        "task_ai",
        "team_tasks",
        "zillow_leads",
    }
)
SHARED = frozenset(
    {
        "apscheduler_service",
        "cron",
        "database",
        "dbos_service",
        "push",
        "schedulers",
        "user",
        "utils",
    }
)
# The shared packages that portfolio code imports today.
PORTFOLIO_MAY_IMPORT = frozenset({"utils"})

# Skipped when scanning the repository, along with every hidden folder (.venv,
# .git, and the other checkouts under .claude/worktrees).
_SKIPPED_DIRS = {"node_modules", "__pycache__"}


def _has_source(directory: Path) -> bool:
    """A folder left behind with only bytecode is not a package."""
    return any(path.is_file() and "__pycache__" not in path.parts for path in directory.rglob("*"))


def _top_level_entries() -> set[str]:
    entries = set()
    for path in SRC.iterdir():
        if path.name.startswith(("_", ".")) or path == TESTS_DIR:
            continue
        if path.is_dir() and _has_source(path):
            entries.add(path.name)
        elif path.suffix == ".py":
            entries.add(path.stem)
    return entries


def _python_files(root: Path):
    for directory, subdirectories, files in os.walk(root):
        subdirectories[:] = [
            name
            for name in subdirectories
            if name not in _SKIPPED_DIRS and not name.startswith(".")
        ]
        for name in files:
            if name.endswith(".py"):
                yield Path(directory) / name


def _imported_modules(path: Path, root: Path = ROOT):
    """Yield (line, module) for every import, with relative imports resolved."""
    package = path.relative_to(root).with_suffix("").parts[:-1]
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom):
            base = package[: len(package) - node.level + 1] if node.level else ()
            module = ".".join([*base, *(node.module.split(".") if node.module else [])])
            yield node.lineno, module
            for alias in node.names:
                yield node.lineno, f"{module}.{alias.name}"


def _api_src_package(module: str) -> str | None:
    parts = module.split(".")
    if parts[:2] == ["api", "src"] and len(parts) > 2:
        return parts[2]
    return None


def test_every_package_under_api_src_is_classified():
    assert not SERNIA_OPERATIONS & SHARED
    classified = {PORTFOLIO} | SERNIA_OPERATIONS | SHARED
    entries = _top_level_entries()
    assert entries - classified == set(), "Classify new packages here and in api/README.md"
    assert classified - entries == set(), "Remove packages that no longer exist"


def test_portfolio_imports_no_sernia_operations_code():
    allowed = {PORTFOLIO} | PORTFOLIO_MAY_IMPORT
    violations = []
    for path in _python_files(PORTFOLIO_DIR):
        for line, module in _imported_modules(path):
            if module == "api" or not module.startswith("api."):
                continue
            package = _api_src_package(module)
            if package in SERNIA_OPERATIONS or package not in allowed:
                violations.append(f"{path.relative_to(ROOT)}:{line} imports {module}")
    assert violations == []


def test_only_the_app_and_tests_import_portfolio():
    violations = []
    for path in _python_files(ROOT):
        if path == APP or path.is_relative_to(PORTFOLIO_DIR) or path.is_relative_to(TESTS_DIR):
            continue
        for line, module in _imported_modules(path):
            if _api_src_package(module) == PORTFOLIO:
                violations.append(f"{path.relative_to(ROOT)}:{line} imports {module}")
    assert violations == []


def test_import_scan_sees_every_import_form(tmp_path):
    package = tmp_path / "api" / "src" / "portfolio" / "demo"
    package.mkdir(parents=True)
    source = package / "routes.py"
    source.write_text(
        "import api.src.sernia_ai.config\n"
        "from api.src import open_phone\n"
        "from api.src.zillow_leads.queue import run\n"
        "from . import agent\n"
        "from .. import public_ai_guard\n"
    )
    modules = {module for _, module in _imported_modules(source, tmp_path)}
    assert {
        "api.src.sernia_ai.config",
        "api.src.open_phone",
        "api.src.zillow_leads.queue",
        "api.src.portfolio.demo.agent",
        "api.src.portfolio.public_ai_guard",
    } <= modules
