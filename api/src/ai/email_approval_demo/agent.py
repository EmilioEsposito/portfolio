"""
Email Approval Demo - Simplified with DBOS Durable Workflows

This uses DBOS's native workflow capabilities to handle:
1. Durable agent execution (survives crashes)
2. Workflow state persistence (PostgreSQL)
3. Human-in-the-loop approval pattern
"""
import os
import logfire
from dataclasses import dataclass

from dbos import DBOS, DBOSConfig, SetWorkflowID
from pydantic_ai import Agent, DeferredToolRequests, DeferredToolResults, ToolDenied
from pydantic_ai.models.openai import OpenAIChatModel

# --- DBOS Configuration (uses app's PostgreSQL) ---
dbos_config: DBOSConfig = {
    "name": "email_approval_demo",
    "database_url": os.getenv(
        "DATABASE_URL",
        "postgresql://portfolio:portfolio123@localhost:5432/portfolio"
    ),
}
DBOS(config=dbos_config)


@dataclass
class EmailAgentDeps:
    """Dependencies for the agent."""
    user_name: str = "User"


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
def send_email(to: str, subject: str, body: str) -> str:
    """Send an email to the specified recipient."""
    logfire.info("Email sent", to=to, subject=subject)
    return f"Email sent to {to} with subject '{subject}'"


# --- DBOS Step Functions (define before workflows that use them) ---
@DBOS.step()
async def run_agent(user_message: str) -> dict:
    """Run the agent (as a DBOS step for checkpointing)."""
    result = await email_agent.run(user_message, deps=EmailAgentDeps())

    if isinstance(result.output, DeferredToolRequests):
        deferred = result.output
        if deferred.approvals:
            approval = list(deferred.approvals)[0]
            args = approval.args_as_dict()
            return {
                "status": "awaiting_approval",
                "tool_call_id": approval.tool_call_id,
                "email": {
                    "to": args.get("to", ""),
                    "subject": args.get("subject", ""),
                    "body": args.get("body", ""),
                },
                "message_history": result.all_messages_json(),
            }

    return {
        "status": "completed",
        "response": str(result.output),
    }


@DBOS.step()
async def resume_agent(
    tool_call_id: str,
    message_history_json: str,
    approved: bool,
    reason: str | None,
) -> dict:
    """Resume the agent with approval decision."""
    from pydantic_ai.messages import ModelMessagesTypeAdapter

    results = DeferredToolResults()
    if approved:
        results.approvals[tool_call_id] = True
    else:
        results.approvals[tool_call_id] = ToolDenied(reason or "Denied by user")
        return {"status": "denied", "reason": reason or "Denied by user"}

    # Parse stored message history
    message_history = ModelMessagesTypeAdapter.validate_json(message_history_json)

    result = await email_agent.run(
        None,
        deps=EmailAgentDeps(),
        message_history=message_history,
        deferred_tool_results=results,
    )

    return {
        "status": "completed",
        "response": str(result.output),
    }


# --- DBOS Workflows (call the step functions directly) ---
@DBOS.workflow()
async def email_workflow(user_message: str) -> dict:
    """
    Start an email workflow. Returns immediately with workflow_id.
    The workflow pauses if approval is needed.
    """
    result = await run_agent(user_message)
    return result


@DBOS.workflow()
async def approve_email_workflow(
    tool_call_id: str,
    message_history_json: str,
    approved: bool,
    reason: str | None = None,
) -> dict:
    """Resume workflow with approval decision."""
    result = await resume_agent(tool_call_id, message_history_json, approved, reason)
    return result


def launch_dbos():
    """Launch DBOS runtime."""
    DBOS.launch()
    logfire.info("DBOS launched for email approval demo")
