"""Keep the lead runtime independent and persisted worker jobs loadable."""

import ast
from pathlib import Path
from unittest.mock import Mock

from apscheduler.util import obj_to_ref, ref_to_obj

from api.src.zillow_leads import queue


def test_lead_package_does_not_import_the_general_agent():
    package = Path(queue.__file__).parent
    # Private eval data may contain arbitrary local files; only inspect source.
    sources = [*package.glob("*.py"), *(package / "evals").glob("*.py")]
    for source in sources:
        for node in ast.walk(ast.parse(source.read_text())):
            modules = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
                modules.extend(f"{node.module}.{alias.name}" for alias in node.names)
            assert not any(module.startswith("api.src.sernia_ai") for module in modules), (
                source.name,
                node.lineno,
                modules,
            )


def test_persisted_scheduler_reference_still_resolves():
    old_reference = (
        "api.src.sernia_ai.triggers.zillow_email_event_trigger:process_zillow_email_queue"
    )
    assert ref_to_obj(old_reference) is queue.process_zillow_email_queue
    assert obj_to_ref(queue.process_zillow_email_queue) == (
        "api.src.zillow_leads.queue:process_zillow_email_queue"
    )


def test_registration_sets_a_wake_up_under_the_same_job_id_and_function(monkeypatch):
    """The wake-up keeps the job id and function path production's job store has,
    so the old server can still load it while a deploy runs."""
    from api.src.apscheduler_service import wakeups

    register = Mock()
    monkeypatch.setattr(wakeups, "register_queue", register)
    queue.register_zillow_email_queue()
    register.assert_called_once_with(queue.QUEUE)
    assert queue.QUEUE.job_id == "zillow-email-queue"
    assert queue.QUEUE.run is queue.process_zillow_email_queue
    assert queue.QUEUE.next_due_at is queue.next_due_at
