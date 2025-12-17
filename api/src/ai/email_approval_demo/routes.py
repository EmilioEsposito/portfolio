"""
SMS Approval Demo Routes

Uses DBOS recv/send pattern for human-in-the-loop workflows.
All workflow state is stored durably in DBOS.
"""
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .agent import (
    start_workflow,
    send_approval,
    get_workflow_status,
    get_sms_preview,
    get_pending_workflows,
    clear_sms_preview,
)

router = APIRouter(
    prefix="/ai/sms-approval",
    tags=["sms-approval-demo"],
)


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

    # Give the workflow time to run the agent step and store SMS preview
    # AI agent calls take time (usually 2-4 seconds for OpenAI)
    import asyncio
    await asyncio.sleep(5)

    # Check if workflow has completed
    try:
        status = handle.get_status()
        has_completed = status.output is not None or status.error is not None
    except Exception:
        has_completed = False

    if not has_completed:
        # Workflow is waiting for approval - get SMS preview from DBOS event
        sms_preview = get_sms_preview(workflow_id)
        if not sms_preview:
            # Fallback - shouldn't happen but handle gracefully
            sms_preview = {"to": "", "body": "(Message being generated...)"}

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


@router.get("/status/{workflow_id}")
async def get_status(workflow_id: str):
    """Get workflow status by ID."""
    status = get_workflow_status(workflow_id)
    if status:
        # Add SMS preview if workflow is pending
        if status.get("status") == "PENDING":
            sms = get_sms_preview(workflow_id)
            if sms:
                status["sms"] = sms
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

    # Clear SMS preview from memory
    clear_sms_preview(workflow_id)

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
    return get_pending_workflows()
