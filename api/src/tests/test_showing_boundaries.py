"""Only the outbox hands a notice to a provider. Read from the source, no database.

- Only ``outbox.py`` imports a provider sender (``quo.py``, and the calendar
  adapter when it is added).
- Only a sender module talks to the provider packages (``api.src.open_phone``
  and ``api.src.google``).
- Nothing in the module uses ``send_message`` or ``create_calendar_event``,
  the repo's general senders, which have no guard.
- Only ``outbox.py`` calls a sending method.

A second way out of the module fails here.
"""

import ast
from pathlib import Path

from api.src.showing_bookings import outbox

PACKAGE = Path(outbox.__file__).parent
OUTBOX = "outbox.py"
# The module's own live senders, and the files allowed to be one.
SENDER_MODULES = {"api.src.showing_bookings.quo"}
SENDER_FILES = {"quo.py"}
PROVIDER_PACKAGES = ("api.src.open_phone", "api.src.google")
GENERAL_SENDERS = {"send_message", "create_calendar_event"}
SENDING_METHODS = {"send_text", "text_status", "create_event", "update_event", "cancel_event"}


def imported(tree: ast.AST) -> list[tuple[int, str]]:
    """Every module and ``module.name`` a source imports, with its line."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [(node.lineno, alias.name) for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append((node.lineno, node.module))
            found += [(node.lineno, f"{node.module}.{alias.name}") for alias in node.names]
    return found


def sources() -> dict[str, ast.AST]:
    found = {path.name: ast.parse(path.read_text()) for path in PACKAGE.glob("*.py")}
    assert OUTBOX in found and SENDER_FILES <= set(found)
    return found


def imports_a_sender(tree: ast.AST) -> list[int]:
    return [
        line
        for line, module in imported(tree)
        if any(module == m or module.startswith(f"{m}.") for m in SENDER_MODULES)
    ]


def reaches_a_provider(tree: ast.AST) -> list[int]:
    return [
        line
        for line, module in imported(tree)
        if any(module == p or module.startswith(f"{p}.") for p in PROVIDER_PACKAGES)
    ]


def uses_a_general_sender(tree: ast.AST) -> list[int]:
    lines = [
        line for line, module in imported(tree) if module.rsplit(".", 1)[-1] in GENERAL_SENDERS
    ]
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in GENERAL_SENDERS:
            lines.append(node.lineno)
        elif isinstance(node, ast.Name) and node.id in GENERAL_SENDERS:
            lines.append(node.lineno)
    return lines


def calls_a_sending_method(tree: ast.AST) -> list[int]:
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in SENDING_METHODS
    ]


def test_only_the_outbox_imports_a_sender():
    for name, tree in sources().items():
        if name != OUTBOX:
            assert imports_a_sender(tree) == [], name


def test_only_sender_modules_talk_to_the_provider_packages():
    for name, tree in sources().items():
        if name not in SENDER_FILES:
            assert reaches_a_provider(tree) == [], name


def test_nothing_uses_the_repos_general_senders():
    for name, tree in sources().items():
        assert uses_a_general_sender(tree) == [], name


def test_only_the_outbox_calls_a_sending_method():
    for name, tree in sources().items():
        if name != OUTBOX:
            assert calls_a_sending_method(tree) == [], name
    assert calls_a_sending_method(sources()[OUTBOX])


def test_the_checks_catch_what_they_are_for():
    """Each check finds a violation, so a passing run means something."""
    cases = {
        imports_a_sender: "from api.src.showing_bookings.quo import QuoTextSender",
        reaches_a_provider: "from api.src.open_phone import service",
        uses_a_general_sender: "from api.src.open_phone.service import send_message",
        calls_a_sending_method: "async def f(texts):\n    await texts.send_text('+1', 'hi')",
    }
    for check, source in cases.items():
        assert check(ast.parse(source)), check.__name__
    assert uses_a_general_sender(ast.parse("import x\nx.service.create_calendar_event()"))
    assert reaches_a_provider(ast.parse("import api.src.google.calendar.service"))
