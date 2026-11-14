"""Web runs belong to the server; an SSE connection is only a subscriber.

The accepted prompt and run status live in the existing conversation row. Step
checkpoints retain progress; they never imply completion or approval. No run or
tool action is automatically replayed after a process restart.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import logfire
from fastapi import HTTPException
from pydantic_ai import CallToolsNode, CancellationToken, ModelRequestNode, capture_run_messages
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.capabilities.abstract import AgentNode, NodeResult
from pydantic_ai.exceptions import RunCancelled, RunTimedOut
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter
from pydantic_ai.tools import RunContext
from pydantic_ai.ui.vercel_ai import VercelAIAdapter
from pydantic_ai.ui.vercel_ai.response_types import BaseChunk, ErrorChunk
from pydantic_ai.usage import UsageLimits
from sqlalchemy import select, text, update

from api.src.agent_conversations.identity import AGENT_NAME
from api.src.agent_conversations.models import AgentConversation
from api.src.database.database import AsyncSessionFactory
from api.src.sernia_ai.deps import SerniaDeps
from api.src.sernia_ai.model_config import resolve_active_run_kwargs
from api.src.sernia_ai.runs import finalize_run, save_failed_run

RUN_KEY = "web_chat_run"
MAX_RUN_SECONDS = 15 * 60
_tasks: set[asyncio.Task] = set()
_tokens: dict[tuple[str, str], CancellationToken] = {}


def new_run(request_id: str) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "id": request_id,
        "status": "running",
        "started_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=MAX_RUN_SECONDS + 60)).isoformat(),
    }


def public_run(metadata: dict | None) -> dict | None:
    run = (metadata or {}).get(RUN_KEY)
    if not isinstance(run, dict):
        return None
    run = dict(run)
    if run.get("status") == "running":
        try:
            if datetime.fromisoformat(run["expires_at"]) < datetime.now(UTC):
                run["status"] = "interrupted"
        except (KeyError, TypeError, ValueError):
            run["status"] = "interrupted"
    return run


async def lock_conversation(session, conversation_id: str) -> None:
    # A transaction lock also serializes admission for a not-yet-created row.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:id, 0))"),
        {"id": f"sernia-chat:{conversation_id}"},
    )


def require_idle(metadata: dict | None) -> None:
    run = public_run(metadata)
    if run and run["status"] == "running":
        raise HTTPException(409, "This conversation is still running. Wait or cancel it first.")


async def cancel_run(conversation_id: str, request_id: str) -> dict | None:
    async with AsyncSessionFactory() as session:
        row = await session.get(AgentConversation, conversation_id, with_for_update=True)
        if row is None or row.agent_name != AGENT_NAME:
            raise HTTPException(404, "Conversation not found")
        run = public_run(row.metadata_)
        if run and run["id"] == request_id and run["status"] == "running":
            run["cancel_requested"] = True
            row.metadata_ = {**(row.metadata_ or {}), RUN_KEY: run}
            await session.commit()
            # Fast path on this worker; polling handles a different worker.
            if token := _tokens.get((conversation_id, request_id)):
                token.cancel()
        return run


@dataclass
class ConversationCheckpoint(AbstractCapability[SerniaDeps]):
    """The Rentium checkpoint pattern, targeting Sernia's conversation row."""

    save: Callable[[list[ModelMessage]], Awaitable[None]]

    async def after_node_run(
        self, ctx: RunContext[SerniaDeps], *, node: AgentNode, result: NodeResult
    ) -> NodeResult:
        messages = list(ctx.messages)
        if isinstance(node, CallToolsNode) and isinstance(result, ModelRequestNode):
            messages.append(result.request)
        elif not isinstance(node, ModelRequestNode):
            return result
        try:
            await self.save(messages)
        except Exception:
            logfire.exception(
                "sernia chat checkpoint failed", conversation_id=ctx.deps.conversation_id
            )
        return result


class ChatRun:
    def __init__(
        self, adapter: VercelAIAdapter, history: list[ModelMessage], deps: SerniaDeps, run: dict
    ):
        self.adapter = adapter
        self.history = history
        self.deps = deps
        self.run = run
        self.token = CancellationToken()
        self.queue: asyncio.Queue[BaseChunk | None] = asyncio.Queue(maxsize=256)
        self.connected = True
        self.finished = False

    def start(self) -> None:
        _tokens[(self.deps.conversation_id, self.run["id"])] = self.token
        task = asyncio.create_task(self.produce(), name=f"sernia-chat:{self.deps.conversation_id}")
        _tasks.add(task)  # Strong ownership after the HTTP response disappears.
        task.add_done_callback(_tasks.discard)

    def emit(self, chunk: BaseChunk | None) -> None:
        if not self.connected:
            return
        try:
            self.queue.put_nowait(chunk)
        except asyncio.QueueFull:
            # A suspended/slow browser must neither block the agent nor grow RAM.
            self.connected = False
            while not self.queue.empty():
                self.queue.get_nowait()
            self.queue.put_nowait(
                ErrorChunk(error_text="Live updates paused. Reconnecting to saved progress.")
            )
            self.queue.put_nowait(None)

    async def events(self) -> AsyncIterator[BaseChunk]:
        try:
            while (chunk := await self.queue.get()) is not None:
                yield chunk
        finally:
            self.connected = False
            while not self.queue.empty():
                self.queue.get_nowait()

    async def checkpoint(self, messages: list[ModelMessage]) -> None:
        async with AsyncSessionFactory() as session:
            await session.execute(
                update(AgentConversation)
                .where(
                    AgentConversation.id == self.deps.conversation_id,
                    AgentConversation.metadata_[RUN_KEY]["id"].as_string() == self.run["id"],
                    AgentConversation.metadata_[RUN_KEY]["status"].as_string() == "running",
                )
                .values(messages=ModelMessagesTypeAdapter.dump_python(messages, mode="json"))
            )
            await session.commit()

    async def watch_cancel(self) -> None:
        while True:
            await asyncio.sleep(1)
            try:
                async with AsyncSessionFactory() as session:
                    metadata = await session.scalar(
                        select(AgentConversation.metadata_).where(
                            AgentConversation.id == self.deps.conversation_id
                        )
                    )
                run = (metadata or {}).get(RUN_KEY, {})
                if run.get("id") != self.run["id"] or run.get("cancel_requested"):
                    self.token.cancel()
                    return
            except Exception:
                logfire.exception(
                    "sernia chat cancel check failed", conversation_id=self.deps.conversation_id
                )

    async def complete(self, result) -> None:
        outcome = await finalize_run(
            result,
            deps=self.deps,
            clerk_user_id=self.deps.user_identifier,
            notify_user_id=self.deps.user_identifier,
            metadata={RUN_KEY: {**self.run, "status": "completed"}},
            expected_run_id=self.run["id"],
        )
        self.finished = outcome.persisted
        if not self.finished:
            raise RuntimeError("Conversation completion could not be saved")

    async def cancelled(self, error: RunCancelled) -> None:
        await self.save_failure(
            error.all_messages(), "failed" if isinstance(error, RunTimedOut) else "cancelled"
        )

    async def save_failure(self, messages: list[ModelMessage], status: str = "failed") -> None:
        await save_failed_run(
            messages or [*self.history, *self.adapter.messages],
            conversation_id=self.deps.conversation_id,
            clerk_user_id=self.deps.user_identifier,
            metadata={RUN_KEY: {**self.run, "status": status}},
            expected_run_id=self.run["id"],
        )
        self.finished = True

    async def produce(self) -> None:
        watcher = asyncio.create_task(self.watch_cancel())
        captured: list[ModelMessage] = []
        try:
            # Never share a request-scoped session with detached execution.
            async with AsyncSessionFactory() as session:
                self.deps.db_session = session
                with capture_run_messages() as captured:
                    kwargs = await resolve_active_run_kwargs()
                    async for chunk in self.adapter.run_stream(
                        message_history=self.history,
                        deps=self.deps,
                        conversation_id=self.deps.conversation_id,
                        cancellation_token=self.token,
                        timeout=MAX_RUN_SECONDS,
                        usage_limits=UsageLimits(request_limit=50),
                        capabilities=[ConversationCheckpoint(save=self.checkpoint)],
                        on_complete=self.complete,
                        on_cancel=self.cancelled,
                        metadata={"trigger_source": "api/sernia-ai/chat"},
                        **kwargs,
                    ):
                        self.emit(chunk)
            # The adapter encodes provider exceptions as SSE error chunks.
            if not self.finished:
                await self.save_failure(captured)
        except asyncio.CancelledError:
            if not self.finished:
                await self.save_failure(captured, "interrupted")
            raise
        except Exception:
            logfire.exception("sernia chat run failed", conversation_id=self.deps.conversation_id)
            if not self.finished:
                await self.save_failure(captured)
            self.emit(
                ErrorChunk(error_text="The response stopped early. Your conversation was saved.")
            )
        finally:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
            _tokens.pop((self.deps.conversation_id, self.run["id"]), None)
            self.emit(None)


async def shutdown_chat_runs() -> None:
    tasks = list(_tasks)
    if not tasks:
        return
    for token in list(_tokens.values()):
        token.cancel()
    _, pending = await asyncio.wait(tasks, timeout=10)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
