"""
Routes for template field detection agent
"""
import json
import functools
from fastapi import APIRouter, HTTPException
from starlette.requests import Request
from starlette.responses import Response
from pydantic_ai.ui.vercel_ai import VercelAIAdapter
import logfire

from api.src.docuform.template_agent.agent import agent, TemplateAgentContext, DOCUMENTS_DIR
from api.src.ai.models import persist_agent_run_result
from api.src.database.database import DBSession

router = APIRouter(prefix="/chat", tags=["docuform"])


@router.post(
    "",
    response_class=Response,
    summary="Template field detection agent chat",
)
async def template_agent_chat(request: Request, session: DBSession) -> Response:
    """
    Chat endpoint for the template field detection agent.

    This endpoint streams responses using the Vercel AI SDK Data Stream Protocol (SSE format).
    Compatible with @ai-sdk/react v2.0.92+ useChat hook.

    The document_filename is passed in the request body alongside messages.

    **Features:**
    - Analyze DOCX documents for potential fields
    - Detect potential fields (placeholders, dates, amounts, party names)
    - Wrap text in content controls with appropriate tags
    - Track modifications and save templates
    """
    # Read request body to get document_filename
    body = await request.body()
    request_json = json.loads(body)
    document_filename = request_json.get("document_filename")
    conversation_id = request_json.get("id")

    if not document_filename:
        raise HTTPException(status_code=400, detail="document_filename is required in request body")

    # Validate file exists
    file_path = DOCUMENTS_DIR / document_filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"Document '{document_filename}' not found")

    logfire.info("Template agent chat request", document=document_filename, conversation_id=conversation_id)

    # Create context with the specified document and conversation_id for working copy isolation
    # NOTE: Dynamic @agent.system_prompt doesn't run when message_history is non-empty
    # (which is always the case with VercelAIAdapter), so we pre-load here
    deps = TemplateAgentContext(
        document_filename=document_filename,
        conversation_id=conversation_id,
    )
    success, message = deps.load_document()
    if not success:
        raise HTTPException(status_code=500, detail=f"Failed to load document: {message}")

    logfire.info("Document pre-loaded", document=document_filename, success=success)

    # Create on_complete callback for persistence
    on_complete_callback = functools.partial(
        persist_agent_run_result,
        conversation_id=conversation_id,
        agent_name=agent.name,
        clerk_user_id="serniacapital",  # All docuform users are @serniacapital.com
        session=session
    )

    # Use dispatch_request which handles deps properly
    response = await VercelAIAdapter.dispatch_request(
        request,
        agent=agent,
        deps=deps,
        on_complete=on_complete_callback,
    )

    return response
