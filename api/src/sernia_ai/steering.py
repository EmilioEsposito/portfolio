"""Text follow-ups for a single server-owned web turn, across API workers."""

from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException
from pydantic_ai import DeferredToolRequests, ModelRequestNode, RunContext
from pydantic_ai.capabilities import AbstractCapability, ProcessHistory
from pydantic_ai.capabilities.abstract import AgentNode, CapabilityOrdering, NodeResult
from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelRequest, UserPromptPart
from pydantic_ai.models import ModelRequestContext
from pydantic_graph import End
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.agent_conversations.identity import AGENT_NAME
from api.src.agent_conversations.models import AgentConversation
from api.src.agent_conversations.run_state import FOLLOW_UPS_KEY, RUN_KEY, public_run
from api.src.database.database import AsyncSessionFactory
from api.src.sernia_ai.deps import SerniaDeps


async def _locked_state(
    session: AsyncSession, conversation_id: str
) -> tuple[dict[str, Any], str | None]:
    # Checkpoints can contain large attachments. Only load the metadata for admission.
    row = (
        await session.execute(
            select(AgentConversation.metadata_, AgentConversation.clerk_user_id)
            .where(
                AgentConversation.id == conversation_id, AgentConversation.agent_name == AGENT_NAME
            )
            .with_for_update()
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(404, "Conversation not found")
    return row.metadata_ or {}, row.clerk_user_id


async def queue_follow_up(
    conversation_id: str, request_id: str, input_id: str, text: str, user_id: str
) -> dict[str, Any]:
    async with AsyncSessionFactory() as session, session.begin():
        metadata, owner = await _locked_state(session, conversation_id)
        run = public_run(metadata)
        if not run or run["id"] != request_id:
            raise HTTPException(409, "This response has ended.")
        if owner != user_id:
            raise HTTPException(403, "Only the person who started this response can steer it.")
        entries = metadata.get(FOLLOW_UPS_KEY, [])
        for entry in entries:
            if entry["id"] == input_id:
                if entry["text"] != text:
                    raise HTTPException(409, "This follow-up ID was already used.")
                return entry
        if (
            run["status"] != "running"
            or not run.get("accepts_follow_ups")
            or run.get("cancel_requested")
        ):
            raise HTTPException(409, "This response is no longer accepting follow-ups.")
        if len(entries) >= 32:
            raise HTTPException(409, "This response has reached its follow-up limit.")
        entry = {"id": input_id, "text": text, "included": False}
        await session.execute(
            update(AgentConversation)
            .where(AgentConversation.id == conversation_id)
            .values(metadata_={**metadata, FOLLOW_UPS_KEY: [*entries, entry]})
        )
        return entry


@dataclass
class ChatSteering(AbstractCapability[SerniaDeps]):
    request_id: str
    staged: set[str] = field(default_factory=set, init=False)

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(wraps=[ProcessHistory])

    async def stage(
        self, ctx: RunContext[SerniaDeps], *, ending: bool = False, paused: bool = False
    ) -> None:
        cid = ctx.deps.conversation_id
        async with AsyncSessionFactory() as session, session.begin():
            metadata, _ = await _locked_state(session, cid)
            run = public_run(metadata)
            if not run or run["id"] != self.request_id or run["status"] != "running":
                raise RuntimeError("Chat run expired or was superseded")
            entries = [
                entry
                for entry in metadata.get(FOLLOW_UPS_KEY, [])
                if not entry["included"] and entry["id"] not in self.staged
            ]
            if paused or (ending and not entries and not ctx.pending_messages):
                # Serialize the final empty check with admission. An approval must
                # keep its deferred calls intact, even when a correction is waiting.
                await session.execute(
                    update(AgentConversation)
                    .where(AgentConversation.id == cid)
                    .values(metadata_={**metadata, RUN_KEY: {**run, "accepts_follow_ups": False}})
                )
                return
            if run.get("cancel_requested"):
                return
            for entry in entries:
                ctx.enqueue(
                    ModelRequest(
                        parts=[UserPromptPart(entry["text"])],
                        metadata={"follow_up_id": entry["id"]},
                    ),
                    priority="asap",
                )
                self.staged.add(entry["id"])

    async def before_node_run(self, ctx: RunContext[SerniaDeps], *, node: AgentNode) -> AgentNode:
        if isinstance(node, ModelRequestNode):
            await self.stage(ctx)
        return node

    async def before_model_request(
        self, ctx: RunContext[SerniaDeps], request_context: ModelRequestContext
    ) -> ModelRequestContext:
        if self.staged:
            # PydanticAI has now drained the queue. Commit the receipt together
            # with the actual transcript before compaction or inference can fail.
            cid = ctx.deps.conversation_id
            async with AsyncSessionFactory() as session, session.begin():
                metadata, _ = await _locked_state(session, cid)
                if metadata[RUN_KEY]["id"] != self.request_id:
                    raise RuntimeError("Chat run was superseded")
                entries = [
                    {**entry, "included": True} if entry["id"] in self.staged else entry
                    for entry in metadata[FOLLOW_UPS_KEY]
                ]
                await session.execute(
                    update(AgentConversation)
                    .where(AgentConversation.id == cid)
                    .values(
                        metadata_={**metadata, FOLLOW_UPS_KEY: entries},
                        messages=ModelMessagesTypeAdapter.dump_python(ctx.messages, mode="json"),
                    )
                )
            self.staged.clear()
        return request_context

    async def after_node_run(
        self, ctx: RunContext[SerniaDeps], *, node: AgentNode, result: NodeResult
    ) -> NodeResult:
        if isinstance(result, End):
            await self.stage(
                ctx, ending=True, paused=isinstance(result.data.output, DeferredToolRequests)
            )
        return result
