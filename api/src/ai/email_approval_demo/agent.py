"""
SMS Approval Demo - Human-in-the-Loop with DBOS

Uses DBOS's recv/send pattern for durable human-in-the-loop workflows:
1. Workflow starts agent → if deferred, calls DBOS.recv() to wait
2. External API calls DBOS.send() with approval decision
3. Workflow resumes automatically and sends the SMS

No custom database tables needed - DBOS handles all state persistence.
"""
import os
import logfire

from dbos import DBOS, SetWorkflowID
from pydantic_ai import Agent, DeferredToolRequests, DeferredToolResults, ToolDenied
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.messages import ModelMessagesTypeAdapter

# Default recipient (Emilio)
DEFAULT_TO_PHONE = os.getenv("EMILIO_PHONE", "+14123703550")

# In-memory store for SMS previews (workflow_id -> sms_preview)
# This is used to pass SMS preview from workflow to API routes
_sms_previews: dict[str, dict] = {}

# --- Agent Definition ---
sms_agent = Agent(
    OpenAIChatModel("gpt-4o-mini"),
    name="sms_approval_agent",
    system_prompt=f"""You are a helpful assistant that sends SMS messages.
When asked to send an SMS or text message, IMMEDIATELY use the send_sms tool.
Be creative and write engaging, personalized messages.
The default recipient is Emilio at {DEFAULT_TO_PHONE} unless otherwise specified.
Do not ask for confirmation - the tool has approval safeguards.""",
    output_type=[str, DeferredToolRequests],
    retries=2,
    instrument=True,
)


@sms_agent.tool_plain(requires_approval=True)
def send_sms(to: str, body: str) -> str:
    """
    Send an SMS message to the specified phone number.

    Args:
        to: Phone number in E.164 format (e.g., +14123703550)
        body: The message content to send
    """
    # This actually sends the SMS via OpenPhone API
    import asyncio
    from api.src.open_phone.service import send_message

    logfire.info("Sending SMS", to=to, body_preview=body[:50])

    # Run async function in sync context
    loop = asyncio.get_event_loop()
    if loop.is_running():
        # Already in async context - create task
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(asyncio.run, send_message(body, to))
            response = future.result()
    else:
        response = asyncio.run(send_message(body, to))

    if response.status_code in [200, 202]:
        logfire.info("SMS sent successfully", to=to, status_code=response.status_code)
        return f"SMS sent successfully to {to}"
    else:
        error_msg = f"Failed to send SMS: {response.status_code} - {response.text}"
        logfire.error(error_msg)
        return error_msg


# --- DBOS Workflow with Human-in-the-Loop ---
@DBOS.workflow()
async def sms_approval_workflow(user_message: str) -> dict:
    """
    Durable workflow for SMS approval.

    1. Runs the agent
    2. If tool requires approval, waits for DBOS.recv("approval")
    3. Resumes agent with approval decision
    4. Returns final result
    """
    # Step 1: Run the agent
    result = await run_agent_step(user_message)

    if result["status"] != "awaiting_approval":
        return result

    # Step 2: Wait for approval (blocks until DBOS.send is called)
    # Timeout of 24 hours for human approval
    approval_data = DBOS.recv("approval", timeout_seconds=86400)

    if approval_data is None:
        return {"status": "timeout", "reason": "Approval timed out after 24 hours"}

    # Step 3: Resume with approval decision
    return await resume_agent_step(
        tool_call_id=result["tool_call_id"],
        message_history_json=result["message_history"],
        approved=approval_data.get("approved", False),
        reason=approval_data.get("reason"),
    )


@DBOS.step()
async def run_agent_step(user_message: str) -> dict:
    """Run the agent and return result or deferred state."""
    result = await sms_agent.run(user_message)

    if isinstance(result.output, DeferredToolRequests):
        deferred = result.output
        if deferred.approvals:
            approval = list(deferred.approvals)[0]
            args = approval.args_as_dict()
            sms_preview = {
                "to": args.get("to", DEFAULT_TO_PHONE),
                "body": args.get("body", ""),
            }
            # Store SMS preview in both DBOS event and in-memory dict
            workflow_id = DBOS.workflow_id
            _sms_previews[workflow_id] = sms_preview
            DBOS.set_event("sms_preview", sms_preview)
            logfire.info("Stored SMS preview", workflow_id=workflow_id, sms=sms_preview)
            # Decode message_history bytes to string
            message_history = result.all_messages_json()
            if isinstance(message_history, bytes):
                message_history = message_history.decode("utf-8")
            return {
                "status": "awaiting_approval",
                "tool_call_id": approval.tool_call_id,
                "sms": sms_preview,
                "message_history": message_history,
            }

    return {
        "status": "completed",
        "response": str(result.output),
    }


@DBOS.step()
async def resume_agent_step(
    tool_call_id: str,
    message_history_json: str,
    approved: bool,
    reason: str | None,
) -> dict:
    """Resume agent with approval decision."""
    results = DeferredToolResults()
    if approved:
        results.approvals[tool_call_id] = True
    else:
        results.approvals[tool_call_id] = ToolDenied(reason or "Denied by user")
        return {"status": "denied", "reason": reason or "Denied by user"}

    message_history = ModelMessagesTypeAdapter.validate_json(message_history_json)

    result = await sms_agent.run(
        None,
        message_history=message_history,
        deferred_tool_results=results,
    )

    return {
        "status": "completed",
        "response": str(result.output),
    }


def start_workflow(workflow_id: str, user_message: str):
    """Start the SMS approval workflow with a specific ID."""
    with SetWorkflowID(workflow_id):
        handle = DBOS.start_workflow(sms_approval_workflow, user_message)
    return handle


def send_approval(workflow_id: str, approved: bool, reason: str | None = None):
    """Send approval decision to a waiting workflow."""
    DBOS.send(workflow_id, {"approved": approved, "reason": reason}, "approval")


def get_workflow_status(workflow_id: str) -> dict | None:
    """Get workflow status by ID."""
    try:
        handle = DBOS.retrieve_workflow(workflow_id)
        status = handle.get_status()
        return {
            "workflow_id": workflow_id,
            "status": status.status if hasattr(status, 'status') else str(status),
        }
    except Exception:
        return None


def get_sms_preview(workflow_id: str) -> dict | None:
    """Get SMS preview from in-memory store or DBOS event."""
    # Check in-memory store first (faster and works in same process)
    if workflow_id in _sms_previews:
        return _sms_previews[workflow_id]
    # Fall back to DBOS event (for durability across restarts)
    try:
        return DBOS.get_event(workflow_id, "sms_preview", timeout_seconds=0)
    except Exception:
        return None


def clear_sms_preview(workflow_id: str):
    """Clear SMS preview from in-memory store."""
    _sms_previews.pop(workflow_id, None)


def get_pending_workflows() -> list[dict]:
    """Get all pending SMS approval workflows."""
    try:
        # Get all workflows with PENDING status for our workflow function
        workflows = DBOS.list_workflows(
            status="PENDING",
            name="sms_approval_workflow",
        )
        result = []
        for wf in workflows:
            sms = get_sms_preview(wf.workflow_id)
            if sms:  # Only include if we have SMS preview (means it's waiting for approval)
                result.append({
                    "workflow_id": wf.workflow_id,
                    "status": "awaiting_approval",
                    "sms": sms,
                    "created_at": wf.created_at,
                })
        return result
    except Exception as e:
        logfire.error("Failed to get pending workflows", error=str(e))
        return []
