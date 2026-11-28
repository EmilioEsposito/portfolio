"""Only the outbox hands a notice to a provider. Read from the source, no database.

- Only ``outbox.py`` imports the Quo sender (``quo.py``). Only
  ``connections.py`` imports the Google calendar adapter
  (``google_calendar.py``), which it hands out as a ``CalendarSource`` through
  ``calendar_for_connection``, and the reader of the calendar picker's lists
  (``google_lists.py``), which only ever reads.
- Only ``outbox.py`` uses a sending method: a text (``send_text``,
  ``send_group_text``, ``text_status``) or an event write (``create_event``, ``update_event``,
  ``cancel_event``), called or merely referenced. Reading busy times is open to
  any file. Creating the showings calendar at connect time is not an event write.
- Only the three Google files name Google's hosts, so no other file reaches
  Google, and only a sender module talks to the repo's provider packages
  (``api.src.open_phone`` and ``api.src.google``). The one reader,
  ``quo_contacts.py``, lists contacts for the resident lookup, sends nothing
  and is imported only by ``residents.py``; the Lead contact writer,
  ``quo_lead_contacts.py``, writes contacts, sends nothing and is imported only
  by ``lead_contacts.py``; the tenant field rules
  (``api.src.open_phone.tenants``) are pure functions any file may use.
- Nothing in the module uses ``send_message`` or ``create_calendar_event``,
  the repo's general senders, which have no guard.
- The team's alert channel (``push.py``, the Sernia web push) is a sender like
  Quo: only ``outbox.py`` imports it or calls ``send_alert``, and only it talks
  to the push package. The reminders, the host's texts and the alerts reach a
  provider only through the outbox.
- Email is the same: only ``outbox.py`` imports the Gmail sender
  (``gmail_thread.py``) or calls ``send_in_thread``, and only the Gmail sender
  imports the lead agent's module (``zillow_leads.handle``) or names one of
  its sends.

A second way out of the module fails here.
"""

import ast
from pathlib import Path

from api.src.showing_bookings import outbox

PACKAGE = Path(outbox.__file__).parent
OUTBOX = "outbox.py"
# The module's own live senders, and the only files that may import each.
SENDERS = {
    "api.src.showing_bookings.quo": {OUTBOX},
    "api.src.showing_bookings.push": {OUTBOX},
    "api.src.showing_bookings.gmail_thread": {OUTBOX},
    "api.src.showing_bookings.google_calendar": {"connections.py"},
    "api.src.showing_bookings.google_lists": {"connections.py"},
    "api.src.showing_bookings.quo_contacts": {"residents.py"},
    "api.src.showing_bookings.quo_lead_contacts": {"lead_contacts.py"},
}
SENDER_FILES = {"quo.py", "google_calendar.py", "push.py", "gmail_thread.py"}
# The lead agent's module and its Gmail sends: reached only by the Gmail sender.
LEAD_AGENT_MODULE = "api.src.zillow_leads.handle"
LEAD_AGENT_SENDS = {"send_thread_email", "send_reply", "send_email"}
GMAIL_SENDER = "gmail_thread.py"
# Reads Quo's contacts and sends nothing.
READER_FILES = {"quo_contacts.py"}
# Writes the team's Lead contacts in Quo and sends nothing.
CONTACT_WRITER_FILES = {"quo_lead_contacts.py"}
# Pure functions in a provider package: no I/O, no import side effects.
PURE_PROVIDER_MODULES = ("api.src.open_phone.tenants",)
PROVIDER_PACKAGES = ("api.src.open_phone", "api.src.google", "api.src.agent_conversations.push")
GOOGLE_HOSTS = ("googleapis.com", "accounts.google.com")
GOOGLE_FILES = {"google_calendar.py", "google_lists.py", "google_oauth.py"}
GENERAL_SENDERS = {"send_message", "create_calendar_event"}
EVENT_WRITES = {"create_event", "update_event", "cancel_event"}
SENDING_METHODS = {
    "send_text",
    "send_group_text",
    "text_status",
    "send_alert",
    "send_in_thread",
    *EVENT_WRITES,
}


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


def texts() -> dict[str, str]:
    found = {path.name: path.read_text() for path in PACKAGE.glob("*.py")}
    assert OUTBOX in found and SENDER_FILES <= set(found) and GOOGLE_FILES <= set(found)
    return found


def sources() -> dict[str, ast.AST]:
    return {name: ast.parse(text) for name, text in texts().items()}


def imports_of(tree: ast.AST, sender: str) -> list[int]:
    return [
        line
        for line, module in imported(tree)
        if module == sender or module.startswith(f"{sender}.")
    ]


def imports_a_sender(tree: ast.AST) -> list[int]:
    return [line for sender in SENDERS for line in imports_of(tree, sender)]


def reaches_a_provider(tree: ast.AST) -> list[int]:
    return [
        line
        for line, module in imported(tree)
        if any(module == p or module.startswith(f"{p}.") for p in PROVIDER_PACKAGES)
        and not any(module == p or module.startswith(f"{p}.") for p in PURE_PROVIDER_MODULES)
    ]


def names_a_google_host(text: str) -> list[int]:
    return [
        number
        for number, line in enumerate(text.splitlines(), start=1)
        if any(host in line for host in GOOGLE_HOSTS)
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


def uses_a_method(tree: ast.AST, methods: set[str]) -> list[int]:
    """Lines that call or reference one of ``methods`` on anything, including through
    ``getattr``. Defining a method of that name is not a use."""
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in methods:
            lines.append(node.lineno)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and any(isinstance(arg, ast.Constant) and arg.value in methods for arg in node.args)
        ):
            lines.append(node.lineno)
    return lines


def test_each_sender_is_imported_only_where_allowed():
    for name, tree in sources().items():
        for sender, allowed in SENDERS.items():
            if name not in allowed:
                assert imports_of(tree, sender) == [], (name, sender)


def test_only_sender_modules_talk_to_the_provider_packages():
    for name, tree in sources().items():
        if name not in SENDER_FILES | READER_FILES | CONTACT_WRITER_FILES:
            assert reaches_a_provider(tree) == [], name


def test_the_contact_reader_sends_nothing():
    tree = sources()["quo_contacts.py"]
    assert uses_a_method(tree, SENDING_METHODS) == []
    assert uses_a_general_sender(tree) == []
    assert reaches_a_provider(tree)


def test_the_lead_contact_writer_sends_nothing():
    tree = sources()["quo_lead_contacts.py"]
    assert uses_a_method(tree, SENDING_METHODS) == []
    assert uses_a_general_sender(tree) == []
    assert reaches_a_provider(tree)
    assert "/v1/messages" not in texts()["quo_lead_contacts.py"]


def test_only_the_google_files_reach_google():
    for name, text in texts().items():
        if name not in GOOGLE_FILES:
            assert names_a_google_host(text) == [], name


def test_the_calendar_pickers_files_only_read():
    trees = sources()
    for name in ("google_lists.py", "calendar_candidates.py"):
        assert uses_a_method(trees[name], SENDING_METHODS) == [], name
        assert uses_a_general_sender(trees[name]) == [], name
        # Only reads the database, and only GETs from Google.
        assert uses_a_method(trees[name], {"commit", "execute", "flush"}) == [], name
    methods = {
        node.value
        for node in ast.walk(trees["google_lists.py"])
        if isinstance(node, ast.Constant)
        and node.value in {"GET", "POST", "PUT", "PATCH", "DELETE"}
    }
    assert methods == {"GET"}


def test_nothing_uses_the_repos_general_senders():
    for name, tree in sources().items():
        assert uses_a_general_sender(tree) == [], name


def test_only_the_outbox_uses_a_sending_method():
    for name, tree in sources().items():
        if name != OUTBOX:
            assert uses_a_method(tree, SENDING_METHODS) == [], name
    assert uses_a_method(sources()[OUTBOX], SENDING_METHODS)


def test_only_the_gmail_sender_reaches_the_lead_agents_sends():
    """An email leaves only through the outbox's Gmail sender, which uses the lead
    agent's own sending function; no other file imports the lead agent's module or
    names one of its sends."""
    trees = sources()
    for name, tree in trees.items():
        if name == GMAIL_SENDER:
            continue
        assert imports_of(tree, LEAD_AGENT_MODULE) == [], name
        assert uses_a_method(tree, LEAD_AGENT_SENDS) == [], name
        assert not [line for line, module in imported(tree) if module.endswith(".send_email")], name
    gmail = trees[GMAIL_SENDER]
    assert imports_of(gmail, LEAD_AGENT_MODULE)
    assert uses_a_method(gmail, {"send_thread_email"})
    assert uses_a_method(trees[OUTBOX], {"send_in_thread"})


# Reminders, the host's texts and the team's alerts
LATE_NOTICE_FILES = {
    "reminders.py",
    "host_texts.py",
    "delivery_checks.py",
    "alerts.py",
    # The host's morning text and the apply text, and removing old details.
    "daily_texts.py",
    "retention.py",
    "lead_email.py",
    # Sending captured messages by hand, and what each must follow.
    "captured.py",
    "told.py",
}


def test_reminders_host_texts_and_alerts_reach_providers_only_through_the_outbox():
    trees = sources()
    assert LATE_NOTICE_FILES <= set(trees)
    for name in LATE_NOTICE_FILES:
        assert imports_a_sender(trees[name]) == [], name
        assert reaches_a_provider(trees[name]) == [], name
        assert uses_a_method(trees[name], SENDING_METHODS) == [], name
        assert private_outbox_uses(trees[name]) == [], name


def test_only_the_outbox_sends_a_team_alert():
    outbox_tree = sources()[OUTBOX]
    assert uses_a_method(outbox_tree, {"send_alert"})
    assert imports_of(outbox_tree, "api.src.showing_bookings.push")


def test_only_the_outbox_writes_calendar_events():
    """Google event writes, which invite people, are reached only through the outbox."""
    for name, tree in sources().items():
        if name != OUTBOX:
            assert uses_a_method(tree, EVENT_WRITES) == [], name
    outbox_tree = sources()[OUTBOX]
    for method in EVENT_WRITES:
        assert uses_a_method(outbox_tree, {method}), method


def test_the_checks_catch_what_they_are_for():
    """Each check finds a violation, so a passing run means something."""
    sender_import = "from api.src.showing_bookings.google_calendar import GoogleCalendar"
    assert imports_of(ast.parse(sender_import), "api.src.showing_bookings.google_calendar")
    assert imports_a_sender(ast.parse("from api.src.showing_bookings.quo import QuoTextSender"))
    assert reaches_a_provider(ast.parse("from api.src.open_phone import service"))
    assert reaches_a_provider(ast.parse("import api.src.google.calendar.service"))
    assert reaches_a_provider(ast.parse("from api.src.open_phone import tenants"))
    assert not reaches_a_provider(
        ast.parse("from api.src.open_phone.tenants import get_contact_unit")
    )
    assert uses_a_method(
        ast.parse("async def f(t):\n    await t.send_group_text(['+1'], 'hi')"), SENDING_METHODS
    )
    assert names_a_google_host('URL = "https://www.googleapis.com/calendar/v3"')
    assert uses_a_general_sender(ast.parse("from api.src.open_phone.service import send_message"))
    assert uses_a_general_sender(ast.parse("import x\nx.service.create_calendar_event()"))
    assert reaches_a_provider(
        ast.parse("from api.src.agent_conversations.push.service import notify_all_sernia_users")
    )
    assert imports_a_sender(ast.parse("from api.src.showing_bookings.push import PushTeamAlerter"))
    assert uses_a_method(
        ast.parse("async def f(a):\n    await a.send_alert('t', 'b', '/')"), SENDING_METHODS
    )
    writes = {
        "async def f(calendar, e):\n    await calendar.create_event('c', e)",
        "async def f(calendar):\n    cancel = calendar.cancel_event\n    await cancel('c', 'e')",
        "async def f(calendar):\n    await getattr(calendar, 'update_event')('c', 'e', None)",
    }
    for source in writes:
        assert uses_a_method(ast.parse(source), EVENT_WRITES), source
    assert uses_a_method(
        ast.parse("async def f(t):\n    await t.send_text('+1', 'hi')"), SENDING_METHODS
    )
    assert uses_a_method(
        ast.parse("async def f(e, r):\n    await e.send_in_thread(r, 'hi')"), SENDING_METHODS
    )
    assert imports_a_sender(
        ast.parse("from api.src.showing_bookings.gmail_thread import GmailThreadSender")
    )
    lazy = "async def f():\n    from api.src.zillow_leads import handle\n    handle.send_reply()"
    assert imports_of(ast.parse(lazy), LEAD_AGENT_MODULE)
    assert uses_a_method(ast.parse(lazy), LEAD_AGENT_SENDS)
    # Defining an event method, as an adapter does, is not a use.
    definition = (
        "class C:\n    async def create_event(self, calendar_id, event):\n        return 'e'"
    )
    assert uses_a_method(ast.parse(definition), EVENT_WRITES) == []


# The public booking path


CLOUDFLARE_HOSTS = ("challenges.cloudflare.com",)
PUBLIC_PATH_FILES = {"public_routes.py", "bookings.py", "holds.py", "turnstile.py", "host_event.py"}


def private_outbox_uses(tree: ast.AST) -> list[int]:
    """Lines that reach into the outbox past its public functions."""
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "outbox"
        and node.attr.startswith("_")
    ]


def calls_named(tree: ast.AST, name: str) -> list[int]:
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == name
    ]


def test_the_public_path_is_scanned_like_every_other_file():
    assert PUBLIC_PATH_FILES <= set(texts())


def test_the_public_path_reaches_providers_only_through_the_outbox():
    trees = sources()
    for name in PUBLIC_PATH_FILES:
        assert imports_a_sender(trees[name]) == [], name
        assert reaches_a_provider(trees[name]) == [], name
        assert uses_a_method(trees[name], SENDING_METHODS) == [], name
        assert private_outbox_uses(trees[name]) == [], name
    assert private_outbox_uses(ast.parse("outbox._send(item)"))


def test_only_turnstile_reaches_cloudflare():
    for name, text in texts().items():
        if name != "turnstile.py":
            assert not any(host in text for host in CLOUDFLARE_HOSTS), name


def test_every_public_route_is_under_the_public_prefix_and_no_team_route_is():
    from api.src.showing_bookings import public_routes, routes

    public_paths = [route.path for route in public_routes.public_router.routes]
    assert public_paths
    assert all(path.startswith("/showing-bookings/public/") for path in public_paths)
    assert not any(
        route.path.startswith("/showing-bookings/public") for route in routes.router.routes
    )


def test_the_notice_job_is_registered_only_by_the_scheduler_startup():
    index = (PACKAGE.parents[1] / "index.py").read_text()
    tree = ast.parse(index)
    startup = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_apscheduler_startup_async"
    )
    everywhere = calls_named(tree, "register_showing_notices")
    assert len(everywhere) == 1
    assert calls_named(startup, "register_showing_notices") == everywhere
    # That startup task runs only when background automation is on.
    assert "if background_automation_enabled():" in index


def test_the_details_removal_job_is_registered_only_by_the_scheduler_startup():
    index = (PACKAGE.parents[1] / "index.py").read_text()
    tree = ast.parse(index)
    startup = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_apscheduler_startup_async"
    )
    everywhere = calls_named(tree, "register_showing_details_removal")
    assert len(everywhere) == 1
    assert calls_named(startup, "register_showing_details_removal") == everywhere


def test_nothing_in_the_module_reaches_the_details_removal_and_it_sends_nothing():
    """No route, public or not, can remove details: no other file imports it."""
    trees = sources()
    for name, tree in trees.items():
        if name != "retention.py":
            assert imports_of(tree, "api.src.showing_bookings.retention") == [], name
            assert not any(
                module == "api.src.showing_bookings" and "retention" in text
                for module, text in from_package_imports(tree)
            ), name
    retention_tree = trees["retention.py"]
    assert imports_of(retention_tree, "api.src.showing_bookings.outbox") == []
    assert not any("outbox" in text for _, text in from_package_imports(retention_tree))


def from_package_imports(tree: ast.AST) -> list[tuple[str, str]]:
    """``from package import a, b`` lines, as (package, names)."""
    return [
        (node.module, ",".join(alias.name for alias in node.names))
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    ]
