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
from pydantic_ai import Agent, DeferredToolRequests, DeferredToolResults, ToolDenied, ToolApproved, ApprovalRequired, AgentRunResult
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.messages import ModelMessagesTypeAdapter
from api.src.open_phone.service import send_message
import secrets
from api.src.contact.service import get_contact_by_slug
import asyncio
import json
from api.src.dbos_service.dbos_config import launch_dbos, shutdown_dbos

# --- Agent Definition ---
hitl_agent2_dbos = Agent(
    OpenAIChatModel("gpt-4o-mini"),
    name="hitl_agent2_dbos",
    system_prompt=f"""You are a helpful assistant that sends SMS messages.
When asked to send an SMS or text message, IMMEDIATELY use the send_sms tool.
Be creative and write engaging, personalized messages.
The default recipient will be Emilio unless otherwise specified.
Do not ask for confirmation - the tool has approval safeguards.""",
    output_type=[str, DeferredToolRequests],
    retries=2,
    instrument=True,
)



@hitl_agent2_dbos.tool_plain(requires_approval=True)
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

@DBOS.step()
async def start_agent(prompt: str) -> AgentRunResult:
    logfire.info("Starting agent...", prompt=prompt)
    result = await hitl_agent2_dbos.run(user_prompt=prompt)
    assert isinstance(result.output, DeferredToolRequests)
    assert result.output.approvals[0].tool_name == "send_sms"
    assert len(result.output.approvals[0].args)>0
    logfire.info(f"Started agent messages: {result.all_messages()}")
    return result

@DBOS.step()
async def handle_deferred_tool_requests(result: AgentRunResult) -> DeferredToolResults:
    logfire.info("Handling deferred tool requests...", result=result)
    tool_call_id = result.output.approvals[0].tool_call_id

    body = json.loads(result.output.approvals[0].args)["body"]

    override_args = {"body": "Orig body overridden by approval. Orignal body: " + body}

    deferred_tool_results = DeferredToolResults(
        approvals={
            tool_call_id: ToolApproved(override_args=override_args)
        }
    )
    return deferred_tool_results

@DBOS.step()
async def resume_agent(first_run_result: AgentRunResult, deferred_tool_results: DeferredToolResults) -> AgentRunResult:
    logfire.info("Resuming agent...")
    resumed = await hitl_agent2_dbos.run(
        message_history=first_run_result.all_messages(),
        deferred_tool_results=deferred_tool_results
    )
    logfire.info(f"Resumed agent messages: {resumed.all_messages()}")
    return resumed

@DBOS.workflow()
async def hitl_agent2_dbos_workflow(prompt: str) -> str:
    prompt = "send funny haiku to Emilio"
    first_run_result = await start_agent(prompt)
    deferred_tool_results = await handle_deferred_tool_requests(first_run_result)
    resumed = await resume_agent(first_run_result, deferred_tool_results)
    messages = resumed.all_messages()
    for i, message in enumerate(messages):
        print(f"Message {i}:\n{str(message)}\n")

# TODO: Add DBOS decorators to make it a workflow

if __name__ == "__main__":
    launch_dbos()
    asyncio.run(hitl_agent2_dbos_workflow(prompt="send funny haiku to Emilio"))