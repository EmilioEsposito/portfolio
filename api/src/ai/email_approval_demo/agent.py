"""
Email Approval Demo Agent - Human-in-the-Loop with DBOS Durable Execution

This is a hello-world level example demonstrating:
1. PydanticAI agent with a tool requiring human approval
2. DBOS for durable execution (survives crashes/restarts)
3. Deferred tool pattern for long-running approval workflows

DBOS makes the agent runs durable - if the server crashes mid-run, it can resume
from where it left off. This is especially useful for long-running workflows
that involve human approval steps.
"""
import os
import uuid
import logfire
from datetime import datetime, timezone
from typing import Optional
from dataclasses import dataclass

from dbos import DBOS, DBOSConfig
from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.durable_exec.dbos import DBOSAgent

from .models import (
    WorkflowState,
    WorkflowStatus,
    EmailDetails,
)

# In-memory store for workflow states
# Note: In production, you'd use a proper database. The workflow_store tracks
# the human-in-the-loop approval state, while DBOS handles agent run durability.
workflow_store: dict[str, WorkflowState] = {}


@dataclass
class EmailAgentContext:
    """Context passed to the agent during runs."""
    workflow_id: str
    user_name: str = "User"


# --- DBOS Configuration ---
# Use SQLite for simplicity in this demo. In production, use PostgreSQL.
dbos_config: DBOSConfig = {
    "name": "email_approval_demo",
    "database_url": os.getenv(
        "DBOS_DATABASE_URL",
        "sqlite:///email_approval_demo.sqlite"
    ),
}

# Initialize DBOS (must be done before defining DBOSAgent)
_dbos_instance = DBOS(config=dbos_config)


# --- Agent Definition ---
model = OpenAIChatModel("gpt-4o-mini")

email_agent = Agent(
    model=model,
    name="email_approval_agent",  # Required: unique name for DBOS workflow recovery
    system_prompt="""You are a helpful assistant that can send emails on behalf of users.

When a user asks you to send an email, IMMEDIATELY use the send_email tool.
Do not ask for confirmation - just call the tool with the email details provided.
The tool itself has approval safeguards built in.

Be concise in your responses.""",
    output_type=[str, DeferredToolRequests],  # Allow deferred tool requests for approval flow
    retries=2,
    instrument=True,  # Enable Logfire tracing
)


@email_agent.tool_plain(requires_approval=True)
def send_email(to: str, subject: str, body: str) -> str:
    """
    Send an email to the specified recipient.

    Args:
        to: Email address of the recipient
        subject: Subject line of the email
        body: Body content of the email

    Returns:
        Confirmation message after email is sent
    """
    # This is where you would call your actual send_email function
    # For demo purposes, we just log and return a success message
    logfire.info(
        "Email sent successfully",
        to=to,
        subject=subject,
        body_preview=body[:100] if len(body) > 100 else body,
    )
    return f"Email sent successfully to {to} with subject '{subject}'"


# Wrap agent with DBOSAgent for durable execution
# This makes agent.run() calls durable - they checkpoint to the database
# and can resume if the server crashes mid-execution
dbos_agent = DBOSAgent(email_agent)

# Track if DBOS has been launched
_dbos_launched = False


def ensure_dbos_launched():
    """Launch DBOS if not already launched. Call before using dbos_agent."""
    global _dbos_launched
    if not _dbos_launched:
        DBOS.launch()
        _dbos_launched = True
        logfire.info("DBOS launched for email approval demo")


# --- Workflow State Management ---
# These functions manage the human-in-the-loop approval state.
# DBOS handles the durability of agent runs; this handles the approval workflow.

def create_workflow(user_message: str) -> WorkflowState:
    """Create a new workflow and store it."""
    workflow_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    state = WorkflowState(
        workflow_id=workflow_id,
        status=WorkflowStatus.PENDING,
        user_message=user_message,
        created_at=now,
        updated_at=now,
    )
    workflow_store[workflow_id] = state
    return state


def get_workflow(workflow_id: str) -> Optional[WorkflowState]:
    """Get a workflow by ID."""
    return workflow_store.get(workflow_id)


def update_workflow(workflow_id: str, **kwargs) -> Optional[WorkflowState]:
    """Update a workflow's state."""
    state = workflow_store.get(workflow_id)
    if not state:
        return None

    # Create updated state
    state_dict = state.model_dump()
    state_dict.update(kwargs)
    state_dict["updated_at"] = datetime.now(timezone.utc)

    new_state = WorkflowState(**state_dict)
    workflow_store[workflow_id] = new_state
    return new_state


def list_workflows() -> list[WorkflowState]:
    """List all workflows, most recent first."""
    return sorted(
        workflow_store.values(),
        key=lambda w: w.created_at,
        reverse=True,
    )
