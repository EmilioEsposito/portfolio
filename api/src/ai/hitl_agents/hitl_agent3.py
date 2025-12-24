"""
HITL Agent 3 - Human-in-the-Loop with Database Persistence

Simple dual-run pattern:
1. First run: Agent returns DeferredToolRequests → save to DB
2. User reviews pending approval from DB
3. Second run: Load messages from DB + DeferredToolResults → agent resumes

DBOS is optional and only for crash recovery resilience.
"""
import json
import uuid
import logfire
from dataclasses import dataclass
from typing import Any

from pydantic_ai import (
    Agent,
    AgentRunResult,
    DeferredToolRequests,
    DeferredToolResults,
    ToolApproved,
    ToolDenied,
)
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.messages import ModelMessage

from api.src.open_phone.service import send_message
from api.src.contact.service import get_contact_by_slug
from api.src.database.database import AsyncSessionFactory
from api.src.ai.models import (
    save_agent_conversation,
    get_conversation_messages,
    persist_agent_run_result,
    get_agent_conversation,
)


# --- Context ---
@dataclass
class HITLAgentContext:
    """Context passed to the agent during execution."""
    user_id: str = "anonymous"
    conversation_id: str | None = None


# --- Agent Definition ---
hitl_agent3 = Agent(
    OpenAIChatModel("gpt-4o-mini"),
    name="hitl_agent3",
    system_prompt="""You are a helpful assistant that can send SMS messages.
When asked to send an SMS or text message, use the send_sms tool.
Be creative and write engaging, personalized messages.
The default recipient is Emilio unless otherwise specified.
Do not ask for confirmation - the tool has approval safeguards built in.""",
    output_type=[str, DeferredToolRequests],
    retries=2,
    instrument=True,
)


@hitl_agent3.tool_plain(requires_approval=True)
async def send_sms(body: str, to: str | None = None) -> str:
    """
    Send an SMS message to the specified phone number.

    Args:
        body: The message content to send
        to: Phone number in E.164 format (e.g., +14123703550). Defaults to Emilio.
    """
    if to is None:
        contact = await get_contact_by_slug("emilio")
        to = contact.phone_number

    logfire.info("Sending SMS", to=to, body_preview=body[:50] if body else "")

    response = await send_message(body, to)

    if response.status_code in [200, 202]:
        logfire.info("SMS sent successfully", to=to, status_code=response.status_code)
        return f"SMS sent successfully to {to}"
    else:
        error_msg = f"Failed to send SMS: {response.status_code} - {response.text}"
        logfire.error(error_msg)
        return error_msg


# --- Helper Functions ---

def extract_pending_approval(result: AgentRunResult) -> dict | None:
    """
    Extract pending approval info from an agent result.

    Returns None if no approval is pending, otherwise returns:
    {
        "tool_call_id": str,
        "tool_name": str,
        "args": dict,
    }
    """
    if not isinstance(result.output, DeferredToolRequests):
        return None

    if not result.output.approvals:
        return None

    approval = result.output.approvals[0]
    return {
        "tool_call_id": approval.tool_call_id,
        "tool_name": approval.tool_name,
        "args": json.loads(approval.args) if isinstance(approval.args, str) else approval.args,
    }


def extract_pending_approval_from_messages(messages: list[ModelMessage]) -> dict | None:
    """
    Extract pending approval info from stored messages.

    A tool call is pending if:
    1. There's a ToolCallPart in a ModelResponse
    2. There's NO corresponding ToolReturnPart in a subsequent ModelRequest

    This works because:
    - First run: [ModelRequest(user), ModelResponse(ToolCallPart)] - pending
    - After approval: [ModelRequest(user), ModelResponse(ToolCallPart), ModelRequest(ToolReturnPart), ModelResponse(text)] - not pending
    """
    from pydantic_ai.messages import ModelResponse, ModelRequest, ToolCallPart, ToolReturnPart

    # Collect all tool_call_ids that have been returned (executed)
    returned_tool_ids: set[str] = set()
    for msg in messages:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, ToolReturnPart):
                    returned_tool_ids.add(part.tool_call_id)

    # Find tool calls that haven't been returned yet (pending approval)
    for msg in reversed(messages):
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ToolCallPart):
                    if part.tool_call_id not in returned_tool_ids:
                        # This tool call has no corresponding return - it's pending
                        return {
                            "tool_call_id": part.tool_call_id,
                            "tool_name": part.tool_name,
                            "args": json.loads(part.args) if isinstance(part.args, str) else part.args,
                        }
    return None


async def get_conversation_with_pending(conversation_id: str) -> dict | None:
    """
    Get a conversation and check if it has a pending approval.

    Returns:
        {
            "conversation_id": str,
            "messages": list[ModelMessage],
            "pending": dict | None,  # approval info if pending
            "agent_name": str,
            "user_id": str,
        }
    """
    async with AsyncSessionFactory() as session:
        conversation = await get_agent_conversation(session, conversation_id)
        if not conversation:
            return None

        messages = await get_conversation_messages(conversation_id, session=session)
        pending = extract_pending_approval_from_messages(messages)

        return {
            "conversation_id": conversation_id,
            "messages": messages,
            "pending": pending,
            "agent_name": conversation.agent_name,
            "user_id": conversation.user_id,
            "created_at": conversation.created_at,
            "updated_at": conversation.updated_at,
        }


async def run_agent_with_persistence(
    prompt: str,
    conversation_id: str | None = None,
    user_id: str = "anonymous",
    message_history: list[ModelMessage] | None = None,
    deferred_tool_results: DeferredToolResults | None = None,
) -> AgentRunResult:
    """
    Run the agent and persist the conversation to the database.

    This is used for both initial runs and resumed runs after approval.
    """
    conversation_id = conversation_id or str(uuid.uuid4())

    result = await hitl_agent3.run(
        user_prompt=prompt if not message_history else None,
        message_history=message_history,
        deferred_tool_results=deferred_tool_results,
        deps=HITLAgentContext(user_id=user_id, conversation_id=conversation_id),
    )

    # Persist conversation state
    await persist_agent_run_result(
        result=result,
        conversation_id=conversation_id,
        agent_name=hitl_agent3.name,
        user_id=user_id,
    )

    return result


async def resume_with_approval(
    conversation_id: str,
    tool_call_id: str,
    approved: bool,
    override_args: dict | None = None,
    denial_reason: str | None = None,
    user_id: str = "anonymous",
) -> AgentRunResult:
    """
    Resume a paused agent with an approval decision.

    Args:
        conversation_id: ID of the conversation to resume
        tool_call_id: ID of the tool call being approved/denied
        approved: Whether to approve or deny
        override_args: Optional dict to override tool arguments (e.g., {"body": "new message"})
        denial_reason: Reason for denial (if denied)
        user_id: User ID for tracking
    """
    # Load conversation history from database
    async with AsyncSessionFactory() as session:
        messages = await get_conversation_messages(conversation_id, session=session)

    if not messages:
        raise ValueError(f"No conversation found with ID: {conversation_id}")

    # Build approval/denial decision
    if approved:
        decision = ToolApproved(override_args=override_args)
    else:
        decision = ToolDenied(denial_reason or "Denied by user")

    deferred_results = DeferredToolResults(
        approvals={tool_call_id: decision}
    )

    # Resume the agent
    result = await hitl_agent3.run(
        message_history=messages,
        deferred_tool_results=deferred_results,
        deps=HITLAgentContext(user_id=user_id, conversation_id=conversation_id),
    )

    # Persist updated conversation state
    await persist_agent_run_result(
        result=result,
        conversation_id=conversation_id,
        agent_name=hitl_agent3.name,
        user_id=user_id,
    )

    return result


async def list_pending_conversations(agent_name: str = "hitl_agent3", limit: int = 50) -> list[dict]:
    """
    List conversations that have pending approvals.

    Scans recent conversations and checks if they have pending tool calls.
    """
    from sqlalchemy import select, desc
    from api.src.ai.models import AgentConversation

    async with AsyncSessionFactory() as session:
        stmt = (
            select(AgentConversation)
            .where(AgentConversation.agent_name == agent_name)
            .order_by(desc(AgentConversation.updated_at))
            .limit(limit)
        )
        result = await session.execute(stmt)
        conversations = result.scalars().all()

        pending_list = []
        for conv in conversations:
            messages = await get_conversation_messages(conv.id, session=session)
            pending = extract_pending_approval_from_messages(messages)
            if pending:
                pending_list.append({
                    "conversation_id": conv.id,
                    "agent_name": conv.agent_name,
                    "user_id": conv.user_id,
                    "pending": pending,
                    "created_at": conv.created_at.isoformat() if conv.created_at else None,
                    "updated_at": conv.updated_at.isoformat() if conv.updated_at else None,
                })

        return pending_list


# --- Demo/Test ---

if __name__ == "__main__":
    import asyncio

    async def demo():
        print("=== HITL Agent 3 Demo ===\n")

        # Step 1: First run - agent proposes an action
        print("Step 1: Running agent with SMS request...")
        conversation_id = str(uuid.uuid4())

        result = await run_agent_with_persistence(
            prompt="Send a friendly hello to Emilio",
            conversation_id=conversation_id,
            user_id="demo_user",
        )

        pending = extract_pending_approval(result)
        if pending:
            print(f"✓ Agent returned DeferredToolRequests")
            print(f"  Tool: {pending['tool_name']}")
            print(f"  Args: {pending['args']}")
            print(f"  Conversation saved to DB: {conversation_id}\n")

            # Step 2: Simulate user approval with modified message
            print("Step 2: Approving with modified message...")
            modified_body = f"[APPROVED] {pending['args'].get('body', '')}"

            final = await resume_with_approval(
                conversation_id=conversation_id,
                tool_call_id=pending["tool_call_id"],
                approved=True,
                override_args={"body": modified_body},
                user_id="demo_user",
            )

            print(f"✓ Agent resumed and completed")
            print(f"  Final output: {final.output}\n")
        else:
            print(f"Result (no approval needed): {result.output}")

        # Step 3: List pending conversations
        print("Step 3: Listing pending conversations...")
        pending_convs = await list_pending_conversations()
        print(f"  Found {len(pending_convs)} pending conversations")

    asyncio.run(demo())
