"""
Email Approval Demo Routes

Uses DBOSAgent for durable agent execution with PostgreSQL storage.
"""
import uuid
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.database.database import get_session
from .agent import start_email_workflow, resume_email_workflow, launch_dbos
from .models import PendingApproval

router = APIRouter(prefix="/ai/email-approval", tags=["email-approval-demo"])


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
async def start_workflow(
    request: StartRequest,
    db: AsyncSession = Depends(get_session),
):
    """Start an email workflow. Returns workflow_id and status."""
    workflow_id = str(uuid.uuid4())

    # Run the durable agent workflow
    result = await start_email_workflow(request.user_message)

    if result["status"] == "awaiting_approval":
        # Store pending approval in database
        pending = PendingApproval(
            workflow_id=workflow_id,
            tool_call_id=result["tool_call_id"],
            message_history=result["message_history"],
            email_to=result["email"]["to"],
            email_subject=result["email"]["subject"],
            email_body=result["email"]["body"],
        )
        db.add(pending)
        await db.commit()

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
async def get_status(
    workflow_id: str,
    db: AsyncSession = Depends(get_session),
):
    """Get workflow status."""
    result = await db.execute(
        select(PendingApproval).where(PendingApproval.workflow_id == workflow_id)
    )
    pending = result.scalar_one_or_none()

    if pending:
        return {
            "workflow_id": workflow_id,
            "status": "awaiting_approval",
            "email": {
                "to": pending.email_to,
                "subject": pending.email_subject,
                "body": pending.email_body,
            },
        }
    return {"workflow_id": workflow_id, "status": "not_found"}


@router.post("/approve/{workflow_id}")
async def approve(
    workflow_id: str,
    request: ApprovalRequest,
    db: AsyncSession = Depends(get_session),
):
    """Approve or deny a pending email."""
    # Fetch pending approval from database
    result = await db.execute(
        select(PendingApproval).where(PendingApproval.workflow_id == workflow_id)
    )
    pending = result.scalar_one_or_none()

    if not pending:
        raise HTTPException(404, "Workflow not found or already processed")

    # Resume with approval decision
    agent_result = await resume_email_workflow(
        tool_call_id=pending.tool_call_id,
        message_history_json=pending.message_history,
        approved=request.approved,
        reason=request.reason,
    )

    # Delete from database after processing
    await db.execute(
        delete(PendingApproval).where(PendingApproval.workflow_id == workflow_id)
    )
    await db.commit()

    return {
        "workflow_id": workflow_id,
        "status": agent_result["status"],
        "response": agent_result.get("response"),
        "reason": agent_result.get("reason"),
    }


@router.get("/workflows")
async def list_workflows(db: AsyncSession = Depends(get_session)):
    """List pending workflows."""
    result = await db.execute(select(PendingApproval))
    pending_list = result.scalars().all()

    return [
        {
            "workflow_id": p.workflow_id,
            "status": "awaiting_approval",
            "email": {
                "to": p.email_to,
                "subject": p.email_subject,
                "body": p.email_body,
            },
            "created_at": p.created_at.isoformat() if p.created_at else None,
        }
        for p in pending_list
    ]
