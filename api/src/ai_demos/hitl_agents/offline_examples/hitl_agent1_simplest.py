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
import uuid

from dbos import DBOS, SetWorkflowID
from pydantic_ai import (
    Agent, 
    DeferredToolRequests, 
    DeferredToolResults, 
    ToolDenied, 
    ToolApproved, 
    ApprovalRequired,
    AgentRunResult,
)
from pydantic_core import to_jsonable_python
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.messages import ModelMessagesTypeAdapter
from api.src.open_phone.service import send_message
import secrets
from api.src.contact.service import get_contact_by_slug
import asyncio
import json
from pprint import pprint
from api.src.database.database import AsyncSessionFactory
from api.src.ai_demos.models import save_agent_conversation, get_conversation_messages, persist_agent_run_result

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
    # Use a fixed ID or generate one. For this demo, let's generate one.
    conversation_id = str(uuid.uuid4())
    print(f"Starting conversation {conversation_id}")
    
    prompt = "send funny haiku to Emilio"
    result = await hitl_agent1.run(user_prompt=prompt)
    
    # Save conversation state to DB after first run
    await persist_agent_run_result(
        result=result,
        conversation_id=conversation_id,
        agent_name=hitl_agent1.name,
        user_id="emilio_dev"
    )
    print("Saved conversation state after first run")
    
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

    # In a real app, we would use the conversation_id to load messages from DB here if this was a separate process
    loaded_messages = []
    async with AsyncSessionFactory() as session:
        loaded_messages = await get_conversation_messages(conversation_id, session=session)
    
    resumed = await hitl_agent1.run(
        message_history=loaded_messages,
        deferred_tool_results=deferred_results
    )
    
    # # If we didn't save the conversation state to DB, we could just use the result in memory:
    # resumed = await hitl_agent1.run(
    #     message_history=result.all_messages(),
    #     deferred_tool_results=deferred_results
    # )
    
    # Save final state to DB
    await persist_agent_run_result(
        result=resumed,
        conversation_id=conversation_id,
        agent_name=hitl_agent1.name,
        user_id="emilio_dev"
    )
    print("Saved final conversation state")
        
    return resumed


if __name__ == "__main__":
    resumed = asyncio.run(run_agent())
    messages = resumed.all_messages()
    for i, message in enumerate(messages):
        print(f"Message {i}:\n{str(message)}\n")
