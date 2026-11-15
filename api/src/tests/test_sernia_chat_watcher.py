"""The cancel watcher must stop between polls, not be cancelled mid-query.

Cancelling an in-flight asyncpg SELECT marks its span as an error-level record,
which trips the "Error-level records (non-local)" Logfire alert.
"""

import asyncio
from types import SimpleNamespace

import pytest

from api.src.sernia_ai import chat_runs


class _BlockingSession:
    def __init__(self, started: asyncio.Event, release: asyncio.Event, outcome: dict):
        self.started, self.release, self.outcome = started, release, outcome

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def scalar(self, _statement):
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.outcome["cancelled"] = True
            raise
        self.outcome["completed"] = True
        return {chat_runs.RUN_KEY: {"id": "run-1"}}


class _Adapter:
    messages: list = []

    def __init__(self, query_started: asyncio.Event):
        self.query_started = query_started

    async def run_stream(self, **_kwargs):
        # Finish the run while the watcher's poll is mid-query.
        await self.query_started.wait()
        if False:
            yield None


@pytest.mark.asyncio
async def test_finished_run_lets_in_flight_cancel_poll_complete(monkeypatch):
    started, release, outcome = asyncio.Event(), asyncio.Event(), {}
    monkeypatch.setattr(
        chat_runs, "AsyncSessionFactory", lambda: _BlockingSession(started, release, outcome)
    )

    async def no_kwargs():
        return {}

    async def save_failed_run(*_args, **_kwargs):
        return None

    monkeypatch.setattr(chat_runs, "resolve_active_run_kwargs", no_kwargs)
    monkeypatch.setattr(chat_runs, "save_failed_run", save_failed_run)

    run = chat_runs.ChatRun(
        _Adapter(started),
        history=[],
        deps=SimpleNamespace(conversation_id="conv-1", user_identifier="user-1"),
        run={"id": "run-1"},
    )
    produce = asyncio.create_task(run.produce())
    await started.wait()
    await asyncio.sleep(0.05)  # produce() is now waiting on the watcher.
    assert not produce.done()
    release.set()
    await asyncio.wait_for(produce, timeout=2)

    assert outcome == {"completed": True}


@pytest.mark.asyncio
async def test_watcher_exits_promptly_when_stopped():
    run = chat_runs.ChatRun(
        adapter=None,
        history=[],
        deps=SimpleNamespace(conversation_id="conv-1", user_identifier="user-1"),
        run={"id": "run-1"},
    )
    watcher = asyncio.create_task(run.watch_cancel())
    run.stop_watching.set()
    await asyncio.wait_for(watcher, timeout=0.5)
    assert not watcher.cancelled()
