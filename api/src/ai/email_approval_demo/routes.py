"""
SMS Approval Demo Routes

Uses DBOS recv/send pattern for human-in-the-loop workflows.
Workflow state is durable in DBOS; we track SMS previews for the UI.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .agent import start_workflow, send_approval, get_workflow_status, DEFAULT_TO_PHONE

router = APIRouter(
    prefix="/ai/sms-approval",
    tags=["sms-approval-demo"],
)

# Track workflow SMS previews for the UI
# The actual workflow state is durably stored by DBOS
_pending_workflows: dict[str, dict] = {}


class StartRequest(BaseModel):
    user_message: str


class ApprovalRequest(BaseModel):
    approved: bool
    reason: str | None = None


@router.post("/start")
async def start_sms_workflow(request: StartRequest):
    """
    Start an SMS approval workflow.

    The workflow runs the AI agent and if it needs to send an SMS,
    it pauses and waits for approval via DBOS.recv().
    """
    workflow_id = str(uuid.uuid4())

    # Start the DBOS workflow (runs in background)
    handle = start_workflow(workflow_id, request.user_message)

    # Give the workflow time to run the agent step
    import asyncio
    await asyncio.sleep(3)

    # Check if workflow has completed
    # WorkflowStatus has output (result) and error fields
    # If output is None and error is None, workflow is still running (waiting)
    try:
        status = handle.get_status()
        has_completed = status.output is not None or status.error is not None
    except Exception:
        has_completed = False

    if not has_completed:
        # Workflow is still running (waiting for approval)
        sms_preview = _parse_sms_from_message(request.user_message)

        _pending_workflows[workflow_id] = {
            "workflow_id": workflow_id,
            "status": "awaiting_approval",
            "sms": sms_preview,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        return {
            "workflow_id": workflow_id,
            "status": "awaiting_approval",
            "sms": sms_preview,
        }

    # Workflow completed quickly (no approval needed or error)
    try:
        result = handle.get_result()
        return {
            "workflow_id": workflow_id,
            "status": result.get("status", "completed"),
            "response": result.get("response"),
        }
    except Exception as e:
        return {
            "workflow_id": workflow_id,
            "status": "error",
            "message": str(e),
        }


def _parse_sms_from_message(message: str) -> dict:
    """Extract SMS details from user message (best effort)."""
    import re

    to = DEFAULT_TO_PHONE  # Default to Emilio
    body = ""

    # Try to extract phone number
    phone_match = re.search(r'to\s+(\+?[\d\s()-]+)', message, re.IGNORECASE)
    if phone_match:
        # Clean up phone number
        phone = re.sub(r'[\s()-]', '', phone_match.group(1))
        if phone.startswith('+'):
            to = phone
        elif len(phone) == 10:
            to = f"+1{phone}"
        elif len(phone) == 11 and phone.startswith('1'):
            to = f"+{phone}"

    # Try to extract message body - common patterns
    # "send ... message ... saying X"
    saying_match = re.search(r'saying\s+["\']?(.+?)["\']?$', message, re.IGNORECASE)
    if saying_match:
        body = saying_match.group(1).strip().rstrip('.')
    else:
        # "message ... with body X"
        body_match = re.search(r'(?:body|message|text)\s+["\']?(.+?)["\']?$', message, re.IGNORECASE)
        if body_match:
            body = body_match.group(1).strip().rstrip('.')

    return {"to": to, "body": body}


@router.get("/status/{workflow_id}")
async def get_status(workflow_id: str):
    """Get workflow status by ID."""
    status = get_workflow_status(workflow_id)
    if status:
        # Add SMS preview if we have it
        if workflow_id in _pending_workflows:
            status["sms"] = _pending_workflows[workflow_id].get("sms")
        return status
    raise HTTPException(404, "Workflow not found")


@router.post("/approve/{workflow_id}")
async def approve(workflow_id: str, request: ApprovalRequest):
    """
    Approve or deny a pending SMS workflow.

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
