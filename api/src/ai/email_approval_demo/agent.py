"""
Email Approval Demo - Human-in-the-Loop with DBOS

Uses DBOS's recv/send pattern for durable human-in-the-loop workflows:
1. Workflow starts agent → if deferred, calls DBOS.recv() to wait
2. External API calls DBOS.send() with approval decision
3. Workflow resumes automatically and completes

No custom database tables needed - DBOS handles all state persistence.
"""
import logfire

from dbos import DBOS, SetWorkflowID
from pydantic_ai import Agent, DeferredToolRequests, DeferredToolResults, ToolDenied
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.messages import ModelMessagesTypeAdapter

# --- Agent Definition ---
email_agent = Agent(
    OpenAIChatModel("gpt-4o-mini"),
    name="email_approval_agent",
    system_prompt="""You are a helpful assistant that sends emails.
When asked to send an email, IMMEDIATELY use the send_email tool.
Do not ask for confirmation - the tool has approval safeguards.""",
    output_type=[str, DeferredToolRequests],
    retries=2,
    instrument=True,
)


@email_agent.tool_plain(requires_approval=True)
@DBOS.step()
def send_email(to: str, subject: str, body: str) -> str:
    """Send an email to the specified recipient."""
    logfire.info("Email sent", to=to, subject=subject)
    return f"Email sent to {to} with subject '{subject}'"


# --- DBOS Workflow with Human-in-the-Loop ---
@DBOS.workflow()
async def email_approval_workflow(user_message: str) -> dict:
    """
    Durable workflow for email approval.

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
    result = await email_agent.run(user_message)

    if isinstance(result.output, DeferredToolRequests):
        deferred = result.output
        if deferred.approvals:
            approval = list(deferred.approvals)[0]
            args = approval.args_as_dict()
            # Decode message_history bytes to string
            message_history = result.all_messages_json()
            if isinstance(message_history, bytes):
                message_history = message_history.decode("utf-8")
            return {
                "status": "awaiting_approval",
                "tool_call_id": approval.tool_call_id,
                "email": {
                    "to": args.get("to", ""),
                    "subject": args.get("subject", ""),
                    "body": args.get("body", ""),
                },
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

    result = await email_agent.run(
        None,
        message_history=message_history,
        deferred_tool_results=results,
    )

    return {
        "status": "completed",
        "response": str(result.output),
    }


def start_workflow(workflow_id: str, user_message: str):
    """Start the email approval workflow with a specific ID."""
    with SetWorkflowID(workflow_id):
        handle = DBOS.start_workflow(email_approval_workflow, user_message)
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
            "status": status.name if hasattr(status, 'name') else str(status),
        }
    except Exception:
        return None
