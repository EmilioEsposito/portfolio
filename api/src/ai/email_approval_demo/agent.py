"""
Email Approval Demo - Using DBOSAgent for Durable Execution

DBOSAgent automatically wraps agent.run() as a DBOS workflow and model
requests as steps. Custom tools with I/O need explicit @DBOS.step decoration.
"""
import os
import logfire

from dbos import DBOS, DBOSConfig
from pydantic_ai import Agent, DeferredToolRequests, DeferredToolResults, ToolDenied
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.durable_exec.dbos import DBOSAgent
from pydantic_ai.messages import ModelMessagesTypeAdapter

# --- DBOS Configuration ---
dbos_config: DBOSConfig = {
    "name": "email_approval_demo",
    "database_url": os.getenv(
        "DATABASE_URL",
        "postgresql://portfolio:portfolio123@localhost:5432/portfolio"
    ),
}
DBOS(config=dbos_config)

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


# Wrap agent with DBOSAgent for durable execution
durable_agent = DBOSAgent(email_agent)


def launch_dbos():
    """Launch DBOS runtime."""
    DBOS.launch()
    logfire.info("DBOS launched for email approval demo")


async def start_email_workflow(user_message: str) -> dict:
    """Start a durable email workflow."""
    result = await durable_agent.run(user_message)

    if isinstance(result.output, DeferredToolRequests):
        deferred = result.output
        if deferred.approvals:
            approval = list(deferred.approvals)[0]
            args = approval.args_as_dict()
            # all_messages_json() returns bytes, decode to string for storage
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


async def resume_email_workflow(
    tool_call_id: str,
    message_history_json: str,
    approved: bool,
    reason: str | None = None,
) -> dict:
    """Resume workflow with approval decision."""
    results = DeferredToolResults()
    if approved:
        results.approvals[tool_call_id] = True
    else:
        results.approvals[tool_call_id] = ToolDenied(reason or "Denied by user")
        return {"status": "denied", "reason": reason or "Denied by user"}

    message_history = ModelMessagesTypeAdapter.validate_json(message_history_json)

    result = await durable_agent.run(
        None,
        message_history=message_history,
        deferred_tool_results=results,
    )

    return {
        "status": "completed",
        "response": str(result.output),
    }
