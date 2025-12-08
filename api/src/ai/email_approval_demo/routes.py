"""
Email Approval Demo Routes

Uses DBOSAgent for durable agent execution.
"""
import uuid
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .agent import start_email_workflow, resume_email_workflow, launch_dbos

router = APIRouter(prefix="/ai/email-approval", tags=["email-approval-demo"])

# In-memory cache for pending approvals (workflow results)
# In production, you'd query DBOS workflow status instead
_pending_approvals: dict[str, dict] = {}


class StartRequest(BaseModel):
    user_message: str


class ApprovalRequest(BaseModel):
    approved: bool
    reason: str | None = None


@router.on_event("startup")
async def startup():
    """Launch DBOS on router startup."""
    launch_dbos()


@router.post("/start")
async def start_workflow(request: StartRequest):
    """Start an email workflow. Returns workflow_id and status."""
    workflow_id = str(uuid.uuid4())

    # Run the durable agent workflow
    result = await start_email_workflow(request.user_message)

    if result["status"] == "awaiting_approval":
        # Store pending approval data
        _pending_approvals[workflow_id] = result
        return {
            "workflow_id": workflow_id,
            "status": "awaiting_approval",
            "email": result["email"],
        }

    return {
        "workflow_id": workflow_id,
        "status": result["status"],
        "response": result.get("response"),
    }


@router.get("/status/{workflow_id}")
async def get_status(workflow_id: str):
    """Get workflow status."""
    if workflow_id in _pending_approvals:
        data = _pending_approvals[workflow_id]
        return {
            "workflow_id": workflow_id,
            "status": "awaiting_approval",
            "email": data["email"],
        }
    return {"workflow_id": workflow_id, "status": "not_found"}


@router.post("/approve/{workflow_id}")
async def approve(workflow_id: str, request: ApprovalRequest):
    """Approve or deny a pending email."""
    if workflow_id not in _pending_approvals:
        raise HTTPException(404, "Workflow not found or already processed")

    pending = _pending_approvals.pop(workflow_id)

    # Resume with approval decision
    result = await resume_email_workflow(
        tool_call_id=pending["tool_call_id"],
        message_history_json=pending["message_history"],
        approved=request.approved,
        reason=request.reason,
    )

    return {
        "workflow_id": workflow_id,
        "status": result["status"],
        "response": result.get("response"),
        "reason": result.get("reason"),
    }


@router.get("/workflows")
async def list_workflows():
    """List pending workflows."""
    return [
        {"workflow_id": wid, "status": "awaiting_approval", "email": data["email"]}
        for wid, data in _pending_approvals.items()
    ]
