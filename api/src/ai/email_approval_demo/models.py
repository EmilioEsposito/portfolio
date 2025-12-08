"""Pydantic models for the email approval demo workflow."""
from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel


class WorkflowStatus(str, Enum):
    """Status of the email approval workflow."""
    PENDING = "pending"  # Workflow started, agent thinking
    AWAITING_APPROVAL = "awaiting_approval"  # Email tool called, waiting for human
    APPROVED = "approved"  # Human approved, email being sent
    DENIED = "denied"  # Human denied the email
    COMPLETED = "completed"  # Workflow finished successfully
    FAILED = "failed"  # Workflow failed with an error


class EmailDetails(BaseModel):
    """Details of the email to be sent."""
    to: str
    subject: str
    body: str


class WorkflowState(BaseModel):
    """State of a single workflow run."""
    workflow_id: str
    status: WorkflowStatus
    user_message: str
    email_details: Optional[EmailDetails] = None
    tool_call_id: Optional[str] = None
    agent_response: Optional[str] = None
    error: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class StartWorkflowRequest(BaseModel):
    """Request to start a new email workflow."""
    user_message: str


class StartWorkflowResponse(BaseModel):
    """Response after starting a workflow."""
    workflow_id: str
    status: WorkflowStatus
    message: str


class ApprovalRequest(BaseModel):
    """Request to approve or deny an email."""
    approved: bool
    reason: Optional[str] = None


class ApprovalResponse(BaseModel):
    """Response after processing approval."""
    workflow_id: str
    status: WorkflowStatus
    message: str
