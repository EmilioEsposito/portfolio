"""
HITL Agent 3 - Human-in-the-Loop with Database Persistence

Simple dual-run pattern:
1. First run: Agent returns DeferredToolRequests → save to DB
2. User reviews pending approval from DB
3. Second run: Load messages from DB + DeferredToolResults → agent resumes

DBOS is optional and only for crash recovery resilience.
"""
import uuid
import logfire
from dataclasses import dataclass

from pydantic_ai import Agent, AgentRunResult, DeferredToolRequests
from pydantic_ai.durable_exec.dbos import DBOSAgent
from dbos import DBOS
from pydantic_ai.models.openai import OpenAIChatModel
from api.src.open_phone.service import send_message
from api.src.contact.service import get_contact_by_slug
from api.src.ai_demos.agent_run_patching import patch_run_with_persistence
from sqlalchemy.ext.asyncio import AsyncSession
from api.src.ai_demos.hitl_utils import (
    extract_pending_approvals as _extract_pending_approvals,
    extract_pending_approval as _extract_pending_approval,
    ApprovalDecision,
    resume_with_approvals as _resume_with_approvals_generic,
)

# --- Context ---
@dataclass
class HITLAgentContext:
    """Context passed to the agent during execution."""
    clerk_user_id: str = "anonymous"
    conversation_id: str | None = None
    db_session: AsyncSession | None = None


# --- Agent Definition ---
hitl_sms_agent = Agent(
    OpenAIChatModel("gpt-4o-mini"),
    name="hitl_sms_agent",
    system_prompt="""You are a helpful assistant that can send SMS messages.
When asked to send an SMS or text message, use the send_sms tool.
Be creative and write engaging, personalized messages.
The default recipient is Emilio unless otherwise specified.
Do not ask for confirmation - the tool has approval safeguards built in.""",
    output_type=[str, DeferredToolRequests],
    retries=2,
    instrument=True,
)

@DBOS.step()
@hitl_sms_agent.tool_plain(requires_approval=True)
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

# Apply persistence patch - automatically persists conversation after each run
patch_run_with_persistence(hitl_sms_agent)

# # DBOS - Leaving commented out because not yet needed and breaks streaming.
# dbos_hitl_sms_agent = DBOSAgent(hitl_sms_agent, name="dbos_hitl_sms_agent")
# patch_run_with_persistence(dbos_hitl_sms_agent)


# --- Helper Functions (delegated to shared hitl_utils) ---

# Re-export for backward compatibility (hitl_agents/routes.py imports from here)
extract_pending_approval = _extract_pending_approval
extract_pending_approvals = _extract_pending_approvals
# ApprovalDecision is imported from hitl_utils and re-exported above


async def resume_with_approvals(
    conversation_id: str,
    decisions: list[ApprovalDecision],
    clerk_user_id: str = "anonymous",
    session: AsyncSession | None = None,
) -> AgentRunResult:
    """
    Resume the HITL SMS agent with approval decisions.
    Thin wrapper around the shared resume_with_approvals.
    """
    return await _resume_with_approvals_generic(
        agent=hitl_sms_agent,
        conversation_id=conversation_id,
        decisions=decisions,
        deps=HITLAgentContext(clerk_user_id=clerk_user_id, conversation_id=conversation_id, db_session=session),
        clerk_user_id=clerk_user_id,
        session=session,
    )


# --- Demo/Test ---

if __name__ == "__main__":
    import asyncio

    # launch_dbos()

    async def demo():
        print("=== HITL SMS Agent Demo ===\n")

        # Step 1: First run - agent proposes an action
        print("Step 1: Running agent with SMS request...")
        conversation_id = str(uuid.uuid4())

        # Use agent.run directly - persistence patch handles saving to DB
        result = await hitl_sms_agent.run(
            user_prompt="Send a friendly hello to Emilio",
            deps=HITLAgentContext(clerk_user_id="demo_user", conversation_id=conversation_id),
        )

        pending_list = extract_pending_approvals(result)
        if pending_list:
            print(f"✓ Agent returned DeferredToolRequests with {len(pending_list)} pending approval(s)")
            for p in pending_list:
                print(f"  - Tool: {p['tool_name']}, Args: {p['args']}")

            # Step 2: Simulate user approval with modified message
            print("Step 2: Approving all pending tool calls...")
            decisions = [
                ApprovalDecision(
                    tool_call_id=p["tool_call_id"],
                    approved=True,
                    override_args={"body": f"[APPROVED] {p['args'].get('body', '')}"} if p['tool_name'] == 'send_sms' else None,
                )
                for p in pending_list
            ]

            final = await resume_with_approvals(
                conversation_id=conversation_id,
                decisions=decisions,
                clerk_user_id="demo_user",
            )

            print(f"✓ Agent resumed and completed")
            print(f"  Final output: {final.output}\n")
        else:
            print(f"Result (no approval needed): {result.output}")

    asyncio.run(demo())
