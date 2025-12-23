"""
SMS Approval Demo - Human-in-the-Loop with DBOS

Uses DBOS's recv/send pattern for durable human-in-the-loop workflows:
1. Workflow starts agent → if deferred, calls DBOS.recv() to wait
2. External API calls DBOS.send() with approval decision
3. Workflow resumes automatically and sends the SMS

No custom database tables needed - DBOS handles all state persistence.
"""
from pydantic_ai.tools import DeferredToolRequests


import os
import time
import logfire

from dbos import DBOS, SetWorkflowID
from pydantic_ai import Agent, DeferredToolRequests, DeferredToolResults, ToolDenied, ToolApproved, ApprovalRequired
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.messages import ModelMessagesTypeAdapter
from api.src.open_phone.service import send_message
import secrets
from api.src.contact.service import get_contact_by_slug
import asyncio
import json

# --- Agent Definition ---
hitl_agent1 = Agent(
    OpenAIChatModel("gpt-4o-mini"),
    name="hitl_agent1",
    system_prompt=f"""You are a helpful assistant that sends SMS messages.
When asked to send an SMS or text message, IMMEDIATELY use the send_sms tool.
Be creative and write engaging, personalized messages.
The default recipient will be Emilio unless otherwise specified.
Do not ask for confirmation - the tool has approval safeguards.""",
    output_type=[str, DeferredToolRequests],
    retries=2,
    instrument=True,
)



@hitl_agent1.tool_plain(requires_approval=True)
async def send_sms(body: str, to: str | None = None) -> str:
    """
    Send an SMS message to the specified phone number.

    Args:
        to: Phone number in E.164 format (e.g., +14123703550)
        body: The message content to send
    """

    if to is None:
        to = (await get_contact_by_slug("emilio")).phone_number

    logfire.info("Sending SMS", to=to, body_preview=body[:50])

    # Run async function in sync context
    response = await send_message(body, to)

    if response.status_code in [200, 202]:
        logfire.info("SMS sent successfully", to=to, status_code=response.status_code)
        return f"SMS sent successfully to {to}"
    else:
        error_msg = f"Failed to send SMS: {response.status_code} - {response.text}"
        logfire.error(error_msg)
        return error_msg


async def run_agent():
    prompt = "send funny haiku to Emilio"
    result = await hitl_agent1.run(user_prompt=prompt)
    assert isinstance(result.output, DeferredToolRequests)
    assert result.output.approvals[0].tool_name == "send_sms"
    assert len(result.output.approvals[0].args)>0

    tool_call_id = result.output.approvals[0].tool_call_id

    body = json.loads(result.output.approvals[0].args)["body"]

    override_args = {"body": "Original body overridden by approval. Orignal body: " + body}

    deferred_results = DeferredToolResults(
        approvals={
            tool_call_id: ToolApproved(override_args=override_args)
        }
    )

    resumed = await hitl_agent1.run(
        user_prompt=prompt,
        message_history=result.all_messages(),
        deferred_tool_results=deferred_results
    )
    print(resumed.output)


if __name__ == "__main__":
    asyncio.run(run_agent())
