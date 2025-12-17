"""
Email Approval Demo Routes

Uses DBOS recv/send pattern for human-in-the-loop workflows.
Workflow state is durable in DBOS; we track email previews for the UI.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .agent import start_workflow, send_approval, get_workflow_status

router = APIRouter(
    prefix="/ai/email-approval",
    tags=["email-approval-demo"],
)

# Track workflow email previews for the UI
# The actual workflow state is durably stored by DBOS
_pending_workflows: dict[str, dict] = {}


class StartRequest(BaseModel):
    user_message: str


class ApprovalRequest(BaseModel):
    approved: bool
    reason: str | None = None


@router.post("/start")
async def start_email_workflow(request: StartRequest):
    """
    Start an email approval workflow.

    The workflow runs the AI agent and if it needs to send an email,
    it pauses and waits for approval via DBOS.recv().
    """
    workflow_id = str(uuid.uuid4())

    # Start the DBOS workflow (runs in background)
    handle = start_workflow(workflow_id, request.user_message)

    # Give the workflow time to run the agent step
    import asyncio
    await asyncio.sleep(3)

    # Check if workflow is still pending (waiting for approval)
    status = get_workflow_status(workflow_id)
    status_str = status.get("status", "") if status else ""

    if status and "PENDING" in status_str.upper():
        # Workflow is waiting - try to get intermediate result
        # The run_agent_step stores email details in its return
        # We'll parse the user message for a basic preview
        email_preview = _parse_email_from_message(request.user_message)

        _pending_workflows[workflow_id] = {
            "workflow_id": workflow_id,
            "status": "awaiting_approval",
            "email": email_preview,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        return {
            "workflow_id": workflow_id,
            "status": "awaiting_approval",
            "email": email_preview,
        }

    # Workflow completed quickly (no approval needed)
    try:
        result = handle.get_result()
        return {
            "workflow_id": workflow_id,
            "status": result.get("status", "completed"),
            "response": result.get("response"),
        }
    except Exception as e:
        # Still running, return pending status
        return {
            "workflow_id": workflow_id,
            "status": "pending",
            "message": f"Workflow started, check status later. ({e})",
        }


def _parse_email_from_message(message: str) -> dict:
    """Extract email details from user message (best effort)."""
    import re

    to = ""
    subject = ""
    body = ""

    # Try to extract email address
    email_match = re.search(r'to\s+(\S+@\S+)', message, re.IGNORECASE)
    if email_match:
        to = email_match.group(1).rstrip('.,')

    # Try to extract subject
    subject_match = re.search(r'subject\s+["\']?([^"\']+?)["\']?\s+(?:and|with|body)', message, re.IGNORECASE)
    if subject_match:
        subject = subject_match.group(1).strip()
    else:
        subject_match = re.search(r'subject\s+(.+?)(?:\s+and|\s+body|$)', message, re.IGNORECASE)
        if subject_match:
            subject = subject_match.group(1).strip().rstrip('.')

    # Try to extract body
    body_match = re.search(r'body\s+["\']?(.+?)["\']?$', message, re.IGNORECASE)
    if body_match:
        body = body_match.group(1).strip().rstrip('.')

    return {"to": to, "subject": subject, "body": body}


@router.get("/status/{workflow_id}")
async def get_status(workflow_id: str):
    """Get workflow status by ID."""
    status = get_workflow_status(workflow_id)
    if status:
        # Add email preview if we have it
        if workflow_id in _pending_workflows:
            status["email"] = _pending_workflows[workflow_id].get("email")
        return status
    raise HTTPException(404, "Workflow not found")


@router.post("/approve/{workflow_id}")
async def approve(workflow_id: str, request: ApprovalRequest):
    """
    Approve or deny a pending email workflow.

    This sends a message to the workflow via DBOS.send(),
    which unblocks the DBOS.recv() call in the workflow.
    """
    # Verify workflow exists
    status = get_workflow_status(workflow_id)
    if not status:
        raise HTTPException(404, "Workflow not found")

    # Send approval decision to the workflow
    send_approval(workflow_id, request.approved, request.reason)

    # Remove from pending list
    _pending_workflows.pop(workflow_id, None)

    # Wait for workflow to process
    import asyncio
    await asyncio.sleep(2)

    # Try to get the final result
    try:
        from dbos import DBOS
        handle = DBOS.retrieve_workflow(workflow_id)
        result = handle.get_result()
        return {
            "workflow_id": workflow_id,
            "status": result.get("status", "completed"),
            "response": result.get("response"),
            "reason": result.get("reason"),
        }
    except Exception:
        return {
            "workflow_id": workflow_id,
            "status": "approved" if request.approved else "denied",
            "message": "Approval sent. Workflow completing.",
        }


@router.get("/workflows")
async def list_workflows():
    """List pending workflows awaiting approval."""
    # Return tracked pending workflows
    # Clean up any that are no longer pending in DBOS
    to_remove = []
    for wf_id in _pending_workflows:
        status = get_workflow_status(wf_id)
        if status and "PENDING" not in status.get("status", "").upper():
            to_remove.append(wf_id)

    for wf_id in to_remove:
        _pending_workflows.pop(wf_id, None)

    return list(_pending_workflows.values())
