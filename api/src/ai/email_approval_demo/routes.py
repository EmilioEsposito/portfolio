"""
FastAPI routes for the Email Approval Demo.

Endpoints:
- POST /api/ai/email-approval/start - Start a new workflow
- GET /api/ai/email-approval/status/{workflow_id} - Get workflow status
- POST /api/ai/email-approval/approve/{workflow_id} - Approve/deny email
- GET /api/ai/email-approval/workflows - List all workflows

This demo uses DBOS for durable execution. Agent runs are checkpointed to a
database, so if the server crashes mid-run, the workflow can resume.
"""
import logfire
from fastapi import APIRouter, HTTPException
from pydantic_ai import DeferredToolRequests, DeferredToolResults, ToolDenied

from .agent import (
    dbos_agent,  # Use the DBOS-wrapped agent for durable execution
    ensure_dbos_launched,
    EmailAgentContext,
    create_workflow,
    get_workflow,
    update_workflow,
    list_workflows,
)
from .models import (
    WorkflowStatus,
    WorkflowState,
    EmailDetails,
    StartWorkflowRequest,
    StartWorkflowResponse,
    ApprovalRequest,
    ApprovalResponse,
)

router = APIRouter(prefix="/ai/email-approval", tags=["email-approval-demo"])

# In-memory store for message history (needed to resume workflows)
# In production, you'd persist this to a database
workflow_message_history: dict[str, list] = {}


@router.post("/start", response_model=StartWorkflowResponse)
async def start_workflow(request: StartWorkflowRequest) -> StartWorkflowResponse:
    """
    Start a new email approval workflow.

    The agent will process the user's message. If it decides to send an email,
    the workflow will pause and wait for human approval.

    Uses DBOS for durable execution - if the server crashes, the workflow
    can resume from the last checkpoint.
    """
    # Ensure DBOS is launched before using the agent
    ensure_dbos_launched()

    # Create the workflow
    state = create_workflow(request.user_message)
    workflow_id = state.workflow_id

    logfire.info("Starting email workflow (DBOS durable)", workflow_id=workflow_id)

    try:
        # Run the agent using DBOSAgent for durable execution
        context = EmailAgentContext(workflow_id=workflow_id)
        result = await dbos_agent.run(
            request.user_message,
            deps=context,
        )

        # Check if we got a deferred tool request (needs approval)
        # When the agent calls a tool with requires_approval=True, the output
        # will be a DeferredToolRequests object instead of a string
        if isinstance(result.output, DeferredToolRequests):
            deferred = result.output
            if deferred.approvals:
                # Get the first approval request (we only have one tool)
                approval = list(deferred.approvals)[0]
                tool_call_id = approval.tool_call_id

                # Parse the email details from the tool arguments
                # args is a JSON string, use args_as_dict() to parse it
                args = approval.args_as_dict()
                email_details = EmailDetails(
                    to=args.get("to", ""),
                    subject=args.get("subject", ""),
                    body=args.get("body", ""),
                )

                # Store message history for resuming later
                message_history = result.all_messages()

                # Update workflow to awaiting approval
                update_workflow(
                    workflow_id,
                    status=WorkflowStatus.AWAITING_APPROVAL,
                    email_details=email_details,
                    tool_call_id=tool_call_id,
                )

                # Store message history separately (in-memory for this demo)
                # In production, you'd persist this to a database
                workflow_message_history[workflow_id] = message_history

                logfire.info(
                    "Workflow awaiting approval",
                    workflow_id=workflow_id,
                    email_to=email_details.to,
                )

                return StartWorkflowResponse(
                    workflow_id=workflow_id,
                    status=WorkflowStatus.AWAITING_APPROVAL,
                    message=f"Email to {email_details.to} requires your approval",
                )

        # No deferred tool - agent completed without needing email
        update_workflow(
            workflow_id,
            status=WorkflowStatus.COMPLETED,
            agent_response=str(result.output),
        )

        return StartWorkflowResponse(
            workflow_id=workflow_id,
            status=WorkflowStatus.COMPLETED,
            message=str(result.output),
        )

    except Exception as e:
        logfire.error("Workflow failed", workflow_id=workflow_id, error=str(e))
        update_workflow(
            workflow_id,
            status=WorkflowStatus.FAILED,
            error=str(e),
        )
        raise HTTPException(status_code=500, detail=f"Workflow failed: {str(e)}")


@router.get("/status/{workflow_id}", response_model=WorkflowState)
async def get_workflow_status(workflow_id: str) -> WorkflowState:
    """Get the current status of a workflow."""
    state = get_workflow(workflow_id)
    if not state:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return state


@router.post("/approve/{workflow_id}", response_model=ApprovalResponse)
async def process_approval(
    workflow_id: str,
    request: ApprovalRequest,
) -> ApprovalResponse:
    """
    Approve or deny the email for a workflow.

    If approved, the email will be sent and the workflow will complete.
    If denied, the workflow will be marked as denied.

    Uses DBOS for durable execution during the resume.
    """
    # Ensure DBOS is launched before using the agent
    ensure_dbos_launched()

    state = get_workflow(workflow_id)
    if not state:
        raise HTTPException(status_code=404, detail="Workflow not found")

    if state.status != WorkflowStatus.AWAITING_APPROVAL:
        raise HTTPException(
            status_code=400,
            detail=f"Workflow is not awaiting approval (status: {state.status})",
        )

    if not state.tool_call_id:
        raise HTTPException(
            status_code=400,
            detail="No tool call ID found - workflow state is invalid",
        )

    logfire.info(
        "Processing approval",
        workflow_id=workflow_id,
        approved=request.approved,
    )

    try:
        # Build the deferred tool results
        results = DeferredToolResults()

        if request.approved:
            # Approve the tool call - it will execute
            results.approvals[state.tool_call_id] = True
            update_workflow(workflow_id, status=WorkflowStatus.APPROVED)
        else:
            # Deny the tool call
            reason = request.reason or "User denied the email"
            results.approvals[state.tool_call_id] = ToolDenied(reason)
            update_workflow(workflow_id, status=WorkflowStatus.DENIED)

            return ApprovalResponse(
                workflow_id=workflow_id,
                status=WorkflowStatus.DENIED,
                message=f"Email denied: {reason}",
            )

        # Resume the agent with the approval and message history
        context = EmailAgentContext(workflow_id=workflow_id)

        # Get the stored message history
        message_history = workflow_message_history.get(workflow_id)
        if not message_history:
            raise HTTPException(
                status_code=400,
                detail="Message history not found - cannot resume workflow",
            )

        # Resume agent with the original message history and approval results
        # Don't pass a new user_prompt - just continue from where we left off
        # Uses DBOSAgent for durable execution
        result = await dbos_agent.run(
            None,  # No new prompt when resuming with deferred results
            deps=context,
            message_history=message_history,
            deferred_tool_results=results,
        )

        # Update to completed
        update_workflow(
            workflow_id,
            status=WorkflowStatus.COMPLETED,
            agent_response=str(result.output),
        )

        return ApprovalResponse(
            workflow_id=workflow_id,
            status=WorkflowStatus.COMPLETED,
            message=f"Email sent! Agent response: {result.output}",
        )

    except Exception as e:
        logfire.error(
            "Approval processing failed",
            workflow_id=workflow_id,
            error=str(e),
        )
        update_workflow(
            workflow_id,
            status=WorkflowStatus.FAILED,
            error=str(e),
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to process approval: {str(e)}",
        )


@router.get("/workflows", response_model=list[WorkflowState])
async def list_all_workflows() -> list[WorkflowState]:
    """List all workflows, most recent first."""
    return list_workflows()
