"""
Routes for the Sernia AI agent.

All routes are gated to @serniacapital.com users via a router-level dependency.
The verified Clerk User object is stashed on request.state.sernia_user by the
gate and retrieved by individual handlers via the SerniaUser dependency.
"""
import asyncio
import json
import functools
from typing import Literal

import anthropic
import openai
import logfire
from pydantic_ai import capture_run_messages
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import Response
from pydantic_ai.ui.vercel_ai import VercelAIAdapter
from clerk_backend_api import User

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from api.src.sernia_ai.agent import sernia_agent
from api.src.sernia_ai.config import AGENT_NAME, WORKSPACE_PATH
from api.src.sernia_ai.model_config import (
    AVAILABLE_MODELS,
    DEFAULT_MODEL_KEY,
    DEFAULT_THINKING_EFFORT,
    ModelKey,
    ThinkingEffort,
    get_active_model_key,
    get_active_thinking_effort,
    get_model_choice,
    resolve_active_run_kwargs,
)
from api.src.sernia_ai.models import _IS_PRODUCTION
from api.src.sernia_ai.models import AppSetting
from api.src.sernia_ai.deps import SerniaDeps
from api.src.ai_demos.hitl_utils import (
    extract_pending_approvals,
    extract_tool_results,
    ApprovalDecision,
    resume_with_approvals,
)
from api.src.ai_demos.models import (
    persist_agent_run_result,
    save_agent_conversation,
    get_agent_conversation,
    list_user_conversations,
    get_conversation_messages,
    delete_conversation,
    list_pending_conversations,
    get_conversation_with_pending,
    extract_pending_approval_from_messages,
)
from api.src.sernia_ai.triggers.ai_sms_event_trigger import (
    _fetch_sms_thread,
    _merge_sms_into_history,
    _sanitize_tool_calls,
)
from api.src.sernia_ai.memory.git_sync import commit_and_push
from api.src.sernia_ai.push.routes import router as push_router
from api.src.sernia_ai.push.service import notify_pending_approval
from api.src.sernia_ai.tools._logging import create_logged_task
from api.src.utils.clerk import verify_serniacapital_user
from api.src.database.database import DBSession


# =============================================================================
# SMS history merge helper
# =============================================================================

_SMS_CONV_PREFIX = "ai_sms_from_"


# =============================================================================
# Error handling helpers
# =============================================================================


# Union of provider exception bases we know about. Catching these lets the
# route handlers stay provider-agnostic as we swap MAIN_AGENT_MODEL between
# Anthropic and OpenAI Responses.
LLMAPIError = (anthropic.APIError, openai.APIError)


def _llm_error_response(
    e: Exception,
    context: str = "request",
) -> tuple[int, str]:
    """Map an LLM provider API error to an HTTP status + user-facing message.

    Returns (status_code, user_message) tuple.
    """
    status_error_types = (anthropic.APIStatusError, openai.APIStatusError)
    connection_error_types = (
        anthropic.APIConnectionError,
        anthropic.APITimeoutError,
        openai.APIConnectionError,
        openai.APITimeoutError,
    )
    if isinstance(e, status_error_types):
        status = getattr(e, "status_code", 500)
        # 529 = Overloaded (Anthropic), 429 = Rate limited → both retriable.
        if status in (529, 429):
            return 503, "The AI service is temporarily overloaded. Please try again in a moment."
        # Any other provider-side 5xx → surface as 502 (bad gateway).
        if 500 <= status < 600:
            return 502, "The AI service is temporarily unavailable. Please try again."
        # 4xx → our misconfiguration (auth, bad request, etc.).
        return 500, "An internal error occurred. Please try again."
    if isinstance(e, connection_error_types):
        return 503, "Unable to reach the AI service. Please try again."
    return 500, "An internal error occurred. Please try again."


async def _merge_sms_if_needed(
    conversation_id: str,
    db_messages: list,
) -> list:
    """For SMS conversations, merge live Quo messages into DB history.

    Quo is the source of truth for SMS — messages sent manually or from
    other processes won't be in our DB.  This fetches the live thread
    and interleaves any missing messages.
    """
    if not conversation_id.startswith(_SMS_CONV_PREFIX):
        return db_messages

    digits = conversation_id[len(_SMS_CONV_PREFIX):]
    phone = f"+{digits}"
    try:
        sms_thread = await _fetch_sms_thread(phone)
        if sms_thread:
            return _merge_sms_into_history(db_messages, sms_thread)
    except Exception:
        logfire.exception(
            "Failed to merge SMS thread for web view",
            conversation_id=conversation_id,
        )
    return db_messages


# =============================================================================
# Router-level auth gate
# =============================================================================

async def _sernia_gate(request: Request) -> None:
    """Router-level dependency: verify @serniacapital.com and stash user."""
    user = await verify_serniacapital_user(request)
    request.state.sernia_user = user


async def _get_sernia_user(request: Request) -> User:
    """Per-endpoint dependency: retrieve user set by the router gate."""
    return request.state.sernia_user


SerniaUser = User  # plain type alias — resolved by _get_sernia_user dependency

router = APIRouter(
    prefix="/sernia-ai",
    tags=["sernia-ai"],
    dependencies=[Depends(_sernia_gate)],
)


# Helper to resolve display name from Clerk User
def _display_name(user: User) -> str:
    return f"{user.first_name or ''} {user.last_name or ''}".strip() or "User"


def _sernia_email(user: User) -> str:
    """Extract the @serniacapital.com email from a Clerk User."""
    for ea in user.email_addresses or []:
        addr = ea.email_address if hasattr(ea, "email_address") else str(ea)
        if addr.endswith("@serniacapital.com"):
            return addr
    # Fallback: construct from first name (all Sernia users have one)
    name = (user.first_name or "unknown").lower()
    return f"{name}@serniacapital.com"


# =============================================================================
# Streaming Chat
# =============================================================================

class _ModifiedJsonRequest:
    """Wrapper to provide modified JSON body to VercelAIAdapter."""

    def __init__(self, original_request: Request, modified_body: dict):
        self._original = original_request
        self._body = json.dumps(modified_body).encode()

    async def body(self) -> bytes:
        return self._body

    @property
    def headers(self):
        return self._original.headers

    def __getattr__(self, name):
        return getattr(self._original, name)


@router.post(
    "/chat",
    response_class=Response,
    summary="Chat with Sernia AI assistant",
)
async def chat_sernia(
    request: Request,
    user: SerniaUser = Depends(_get_sernia_user),
    session: DBSession = None,
) -> Response:
    """
    Streaming chat endpoint for the Sernia AI agent.

    Uses PydanticAI's VercelAIAdapter for Vercel AI SDK Data Stream Protocol (SSE).
    Backend DB is the authoritative source for message history.
    Only the latest frontend message is forwarded; history is loaded from DB.
    """
    clerk_user_id = user.id
    user_name = _display_name(user)

    request_json = await request.json()
    conversation_id = request_json.get("id")
    frontend_messages = request_json.get("messages", [])

    # Load message history from DB (authoritative source)
    # clerk_user_id=None for shared team access — all Sernia users see all conversations
    backend_message_history = await get_conversation_messages(
        conversation_id, clerk_user_id=None, session=session, include_terminal=True
    )
    # For SMS conversations, merge live Quo messages (source of truth)
    backend_message_history = await _merge_sms_if_needed(
        conversation_id, backend_message_history
    )
    # Remove trailing unprocessed tool calls (can happen if a previous run crashed)
    backend_message_history = _sanitize_tool_calls(backend_message_history)

    logfire.info(
        "sernia chat",
        conversation_id=conversation_id,
        clerk_user_id=clerk_user_id,
        user_name=user_name,
        user_email=_sernia_email(user),
        frontend_msg_count=len(frontend_messages),
        db_msg_count=len(backend_message_history),
        _tags=["trigger:user"],
    )

    # Only use the LAST message from frontend (the new user input)
    last_message = frontend_messages[-1] if frontend_messages else None
    modified_body = {
        **request_json,
        "messages": [last_message] if last_message else [],
    }
    wrapped_request = _ModifiedJsonRequest(request, modified_body)

    deps = SerniaDeps(
        db_session=session,
        conversation_id=conversation_id or "",
        user_identifier=clerk_user_id,
        user_name=user_name,
        user_email=_sernia_email(user),
        modality="web_chat",
        workspace_path=WORKSPACE_PATH,
    )

    async def _on_complete(result):
        await persist_agent_run_result(
            result,
            conversation_id=conversation_id,
            agent_name=AGENT_NAME,
            clerk_user_id=clerk_user_id,
        )
        create_logged_task(commit_and_push(WORKSPACE_PATH), name="git_sync")

        # Send push notification if there are pending approvals
        pending = extract_pending_approvals(result)
        if pending:
            first = pending[0]
            create_logged_task(
                notify_pending_approval(
                    conversation_id=conversation_id,
                    tool_name=first["tool_name"],
                    tool_args=first.get("args"),
                ),
                name="notify_pending_approval",
            )

    on_complete = _on_complete

    run_kwargs = await resolve_active_run_kwargs()

    try:
        response = await VercelAIAdapter.dispatch_request(
            wrapped_request,
            agent=sernia_agent,
            message_history=backend_message_history if backend_message_history else None,
            deps=deps,
            on_complete=on_complete,
            metadata={"trigger_source": "api/sernia-ai/chat"},
            **run_kwargs,
        )
    except LLMAPIError as e:
        # LLM provider API errors (overloaded, rate limited, etc.) — log but don't
        # treat as internal error, surface with appropriate status code
        status_code, user_message = _llm_error_response(e, "chat")
        logfire.warn(
            "sernia chat LLM API error",
            conversation_id=conversation_id,
            clerk_user_id=clerk_user_id,
            error_type=type(e).__name__,
            provider_status=getattr(e, "status_code", None),
        )
        return Response(
            content=json.dumps({
                "error": user_message,
                "conversation_id": conversation_id or "",
                "retriable": status_code == 503,
            }),
            status_code=status_code,
            media_type="application/json",
        )
    except Exception as e:
        logfire.exception(
            "sernia chat dispatch error",
            conversation_id=conversation_id,
            clerk_user_id=clerk_user_id,
            error_type=type(e).__name__
        )
        return Response(
            content=json.dumps({
                "error": "An internal error occurred. Please try again.",
                "conversation_id": conversation_id or "",
            }),
            status_code=500,
            media_type="application/json",
        )

    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Cache-Control"] = "no-cache, no-transform"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Conversation-Id"] = conversation_id or ""

    return response


# =============================================================================
# Conversation / Approval API
# =============================================================================

class ApprovalDecisionRequest(BaseModel):
    """A single approval decision for one tool call."""
    tool_call_id: str
    approved: bool
    reason: str | None = None
    override_args: dict | None = None


class ApprovalRequest(BaseModel):
    """Batch approval request for one or more tool calls."""
    decisions: list[ApprovalDecisionRequest]
    # Optional user-typed message attached to this approval round. PydanticAI
    # bundles this alongside the ToolReturnParts into a single ModelRequest
    # (see CallToolsNode), so it persists as a real UserPromptPart — used by
    # the "deny with feedback" flow where the user's reply should live in
    # message history as a normal chat turn, not just as a tool-denial reason.
    user_message: str | None = None


@router.post("/conversation/{conversation_id}/approve")
async def approve_conversation(
    conversation_id: str,
    body: ApprovalRequest,
    user: SerniaUser = Depends(_get_sernia_user),
    session: DBSession = None,
):
    """
    Approve or deny pending tool calls for a Sernia agent conversation.
    Resumes the agent with decisions and returns the result.
    """
    # Log each decision verbatim (using repr on override_args values so any
    # hidden \r\n / control chars in edited bodies are visible in logfire).
    decisions_log = [
        {
            "tool_call_id": d.tool_call_id,
            "approved": d.approved,
            "override_args_repr": {k: repr(v) for k, v in (d.override_args or {}).items()} or None,
        }
        for d in body.decisions
    ]
    logfire.info(
        "sernia approve",
        conversation_id=conversation_id,
        decisions=decisions_log,
    )
    clerk_user_id = user.id
    user_name = _display_name(user)

    captured_messages = None
    try:
        decisions = [
            ApprovalDecision(
                tool_call_id=d.tool_call_id,
                approved=d.approved,
                override_args=d.override_args,
                denial_reason=d.reason,
            )
            for d in body.decisions
        ]

        deps = SerniaDeps(
            db_session=session,
            conversation_id=conversation_id,
            user_identifier=clerk_user_id,
            user_name=user_name,
            user_email=_sernia_email(user),
            modality="web_chat",
            workspace_path=WORKSPACE_PATH,
        )

        run_kwargs = await resolve_active_run_kwargs()

        with capture_run_messages() as captured_messages:
            result = await resume_with_approvals(
                agent=sernia_agent,
                conversation_id=conversation_id,
                decisions=decisions,
                deps=deps,
                clerk_user_id=None,  # Shared team access
                session=session,
                metadata={"trigger_source": "api/sernia-ai/approve"},
                user_message=body.user_message,
                **run_kwargs,
            )

        # Persist the approval result (tool outputs + agent follow-up) to DB
        # so that subsequent messages have the full conversation history.
        await persist_agent_run_result(
            result,
            conversation_id=conversation_id,
            agent_name=AGENT_NAME,
            clerk_user_id=clerk_user_id,
        )
        create_logged_task(commit_and_push(WORKSPACE_PATH), name="git_sync")

        # If this is an SMS conversation, send the agent's response back via SMS
        conv = await get_agent_conversation(session, conversation_id, clerk_user_id=None)
        if (
            conv
            and conv.modality == "sms"
            and isinstance(result.output, str)
            and result.output.strip()
            and conv.metadata_
            and conv.metadata_.get("trigger_phone")
        ):
            create_logged_task(
                _send_sms_reply(
                    to_phone=conv.metadata_["trigger_phone"],
                    message=result.output,
                ),
                name="sms_reply",
            )

        pending = extract_pending_approvals(result)
        tool_results = extract_tool_results(result)

        return {
            "conversation_id": conversation_id,
            "output": result.output if isinstance(result.output, str) else None,
            "pending": pending,
            "tool_results": tool_results,
            "status": "pending_approval" if pending else "completed",
            "decisions": [
                {"tool_call_id": d.tool_call_id, "approved": d.approved}
                for d in body.decisions
            ],
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except LLMAPIError as e:
        # LLM provider API errors (overloaded, rate limited, etc.) — log as warning,
        # surface with appropriate status code, don't leak raw error body
        status_code, user_message = _llm_error_response(e, "approve")
        logfire.warn(
            "sernia approve LLM API error",
            conversation_id=conversation_id,
            clerk_user_id=clerk_user_id,
            error_type=type(e).__name__,
            provider_status=getattr(e, "status_code", None),
        )
        if captured_messages:
            try:
                await save_agent_conversation(
                    session=session,
                    conversation_id=conversation_id,
                    agent_name=AGENT_NAME,
                    messages=captured_messages,
                    clerk_user_id=clerk_user_id,
                    metadata={"partial": True, "error": True, "llm_error": True},
                )
            except Exception:
                logfire.exception("failed to save partial approval conversation")
        raise HTTPException(status_code=status_code, detail=user_message)
    except Exception as e:
        logfire.exception(
            "sernia approve error",
            conversation_id=conversation_id,
            clerk_user_id=clerk_user_id,
            error_type=type(e).__name__
        )
        if captured_messages:
            try:
                await save_agent_conversation(
                    session=session,
                    conversation_id=conversation_id,
                    agent_name=AGENT_NAME,
                    messages=captured_messages,
                    clerk_user_id=clerk_user_id,
                    metadata={"partial": True, "error": True},
                )
            except Exception:
                logfire.exception("failed to save partial approval conversation")
        raise HTTPException(status_code=500, detail="An internal error occurred. Please try again.")


@router.get("/conversation/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    user: SerniaUser = Depends(_get_sernia_user),
    session: DBSession = None,
):
    """Get conversation details including pending approval info."""
    conv = await get_conversation_with_pending(conversation_id, clerk_user_id=None, session=session)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    return {
        "conversation_id": conversation_id,
        "pending": conv["pending"],
        "agent_name": conv["agent_name"],
        "clerk_user_id": conv["clerk_user_id"],
        "created_at": conv["created_at"].isoformat() if conv["created_at"] else None,
        "updated_at": conv["updated_at"].isoformat() if conv["updated_at"] else None,
        "status": "pending_approval" if conv["pending"] else "completed",
    }


@router.get("/conversation/{conversation_id}/messages")
async def get_conversation_messages_endpoint(
    conversation_id: str,
    user: SerniaUser = Depends(_get_sernia_user),
    session: DBSession = None,
):
    """Get conversation messages in Vercel AI SDK format."""
    pydantic_messages = await get_conversation_messages(
        conversation_id, clerk_user_id=None, session=session
    )

    # For SMS conversations, merge live Quo messages (source of truth)
    pydantic_messages = await _merge_sms_if_needed(conversation_id, pydantic_messages)

    if not pydantic_messages:
        return {"messages": [], "conversation_id": conversation_id}

    vercel_messages = VercelAIAdapter.dump_messages(pydantic_messages)
    pending = extract_pending_approval_from_messages(pydantic_messages)

    return {
        "messages": [msg.model_dump(by_alias=True) for msg in vercel_messages],
        "conversation_id": conversation_id,
        "pending": pending,
    }


@router.get("/workflow/pending")
async def list_pending_workflows(
    user: SerniaUser = Depends(_get_sernia_user),
    session: DBSession = None,
):
    """List Sernia conversations with pending approvals (team-wide)."""
    pending = await list_pending_conversations(
        agent_name=AGENT_NAME,
        clerk_user_id=None,
        session=session,
    )
    return {"conversations": pending, "count": len(pending)}


@router.get("/conversations/history")
async def get_conversation_history(
    user: SerniaUser = Depends(_get_sernia_user),
    limit: int = 20,
    offset: int = 0,
    modality: str | None = None,
):
    """List recent conversations for all Sernia users (shared team context)."""
    conversations = await list_user_conversations(
        clerk_user_id=None,
        agent_name=AGENT_NAME,
        limit=limit,
        offset=offset,
        modality=modality,
    )
    return {
        "conversations": conversations,
        "count": len(conversations),
    }


@router.delete("/conversation/{conversation_id}")
async def delete_conversation_endpoint(
    conversation_id: str,
    user: SerniaUser = Depends(_get_sernia_user),
):
    """Delete a conversation (any Sernia user can delete any Sernia conversation)."""
    try:
        await delete_conversation(conversation_id, clerk_user_id=None)
        return {"success": True, "conversation_id": conversation_id}
    except ValueError:
        raise HTTPException(status_code=404, detail="Conversation not found")


# =============================================================================
# Admin
# =============================================================================

async def _resolve_tool_overview(deps: SerniaDeps) -> dict:
    """Walk the agent's toolsets and return tool definitions as the model sees them.

    Uses ``Toolset.get_tools(ctx)`` — the same call PydanticAI makes when
    packaging tools for the model — so the preview is bit-for-bit what gets
    injected (modulo per-run builtin_tools added by ``model_config``).
    """
    from pydantic_ai import RunContext
    from pydantic_ai.usage import RunUsage

    ctx: RunContext[SerniaDeps] = RunContext(  # type: ignore[call-arg]
        deps=deps, model=None, usage=RunUsage()
    )

    # Resolve tools first so we can use tool-name shape to label capability-
    # contributed toolsets (the agent wraps them in CombinedToolset with no
    # ``name`` attribute). Each toolset's own ``get_instructions(ctx)`` is also
    # called — that's how SkillsCapability injects the skill registry into the
    # system prompt at run time, and the Context tab needs to surface it for
    # the preview to match what the model actually sees.
    import inspect

    _SKILLS_TOOLS = {"list_skills", "load_skill"}

    def _label_for(ts, tool_names: set[str]) -> str:
        explicit = getattr(ts, "name", None)
        if explicit:
            return explicit
        cls_name = type(ts).__name__
        if cls_name == "_AgentFunctionToolset":
            return "agent.tool registrations"
        if _SKILLS_TOOLS.issubset(tool_names):
            return "skills"
        return cls_name

    toolsets_out: list[dict] = []
    toolset_instructions: list[dict] = []
    total = 0
    for ts in sernia_agent.toolsets:
        try:
            tools = await ts.get_tools(ctx)
        except Exception as e:
            toolsets_out.append({
                "name": getattr(ts, "name", None) or type(ts).__name__,
                "error": f"{type(e).__name__}: {e}",
                "tools": [],
            })
            continue
        label = _label_for(ts, set(tools.keys()))
        entries = []
        for t in tools.values():
            td = t.tool_def
            entries.append({
                "name": td.name,
                "description": td.description or "",
                "parameters_json_schema": td.parameters_json_schema,
                "kind": getattr(td, "kind", None),
                "metadata": getattr(td, "metadata", None) or {},
            })
        total += len(entries)
        toolsets_out.append({"name": label, "tools": entries})

        # Capture toolset-injected instructions (e.g. skill registry).
        gi = getattr(ts, "get_instructions", None)
        if gi is None:
            continue
        try:
            content = gi(ctx)
            if inspect.isawaitable(content):
                content = await content
        except Exception:
            content = None
        if content:
            toolset_instructions.append({"label": label, "content": str(content)})

    # Builtin tools (web search/fetch). The agent stores its construction-time
    # set on a private attr; the active model also adds run-specific builtins
    # (e.g. WebFetchTool on Anthropic) — surface both for an honest picture.
    builtins: list[dict] = []
    seen: set[str] = set()
    base_builtins = getattr(sernia_agent, "_cap_builtin_tools", []) or []
    run_kwargs = await resolve_active_run_kwargs()
    for bt in list(base_builtins) + list(run_kwargs.get("builtin_tools") or []):
        kind = getattr(bt, "kind", type(bt).__name__)
        if kind in seen:
            continue
        seen.add(kind)
        builtins.append({
            "name": kind,
            "type": type(bt).__name__,
            "config": {
                k: v for k, v in (bt.__dict__ if hasattr(bt, "__dict__") else {}).items()
                if not k.startswith("_") and not callable(v)
            },
        })

    return {
        "toolsets": toolsets_out,
        "builtin_tools": builtins,
        "total_tools": total,
        "toolset_instructions": toolset_instructions,
    }


@router.get("/admin/context")
async def get_admin_context(
    user: SerniaUser = Depends(_get_sernia_user),
    modality: Literal["sms", "email", "web_chat"] = "web_chat",
    user_name: str | None = None,
):
    """
    Return the fully resolved system instructions as they would appear
    at the start of an agent run.

    Query params allow mocking the context deps from the frontend:
    - modality: sms | email | web_chat (default: web_chat)
    - user_name: override display name (default: from Clerk user)
    """
    from types import SimpleNamespace

    from api.src.sernia_ai.instructions import STATIC_INSTRUCTIONS, DYNAMIC_INSTRUCTIONS

    resolved_name = user_name or _display_name(user)

    # Build a fake RunContext — the instruction functions only access ctx.deps.*
    deps = SerniaDeps(
        db_session=None,  # type: ignore[arg-type]
        conversation_id="",
        user_identifier=user.id,
        user_name=resolved_name,
        user_email=_sernia_email(user),
        modality=modality,
        workspace_path=WORKSPACE_PATH,
    )
    fake_ctx = SimpleNamespace(deps=deps)

    import inspect

    sections = [{"label": "Static Instructions", "content": STATIC_INSTRUCTIONS}]
    for fn in DYNAMIC_INSTRUCTIONS:
        if inspect.iscoroutinefunction(fn):
            # Async dynamic instructions in this codebase are side-effect-only
            # (e.g. ``refresh_from_remote`` pulls the workspace from GitHub).
            # They contribute "" to the prompt — firing them from a debug
            # preview would run real I/O and mutate process-global state.
            sections.append({"label": fn.__name__, "content": "(skipped — async side-effect only)"})
            continue
        content = fn(fake_ctx)  # type: ignore[arg-type]
        sections.append({"label": fn.__name__, "content": content or "(empty)"})

    tool_overview = await _resolve_tool_overview(deps)
    # Toolsets can also contribute system-prompt content (e.g. SkillsCapability
    # injects the skill registry). Surface those alongside the explicit
    # instructions so the preview matches what the model actually sees.
    for ts_section in tool_overview.pop("toolset_instructions", []):
        sections.append({
            "label": f"toolset:{ts_section['label']}",
            "content": ts_section["content"],
        })
    combined = "\n\n".join(s["content"] for s in sections)

    return {
        "sections": sections,
        "combined": combined,
        "model": get_model_choice(await get_active_model_key()).model_string,
        "deps": {
            "user_name": resolved_name,
            "modality": modality,
        },
        **tool_overview,
    }


@router.get("/conversation/{conversation_id}/context")
async def get_conversation_context(
    conversation_id: str,
    user: SerniaUser = Depends(_get_sernia_user),
    session: DBSession = None,
):
    """
    Return the latest system instructions as they would have been resolved
    for a specific conversation.

    Reconstructs the dynamic instructions using the conversation's stored
    metadata (modality, clerk_user_id, etc.).
    """
    from types import SimpleNamespace

    from api.src.sernia_ai.instructions import STATIC_INSTRUCTIONS, DYNAMIC_INSTRUCTIONS
    from api.src.sernia_ai.config import TRIGGER_BOT_ID, TRIGGER_BOT_NAME, GOOGLE_DELEGATION_EMAIL

    conv = await get_agent_conversation(session, conversation_id, clerk_user_id=None)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Reconstruct deps from conversation record
    modality = conv.modality or "web_chat"

    # Determine user identity from conversation
    is_trigger = conv.clerk_user_id == TRIGGER_BOT_ID
    if is_trigger:
        resolved_name = TRIGGER_BOT_NAME
        resolved_email = GOOGLE_DELEGATION_EMAIL
        user_identifier = TRIGGER_BOT_ID
    else:
        resolved_name = _display_name(user)
        resolved_email = _sernia_email(user)
        user_identifier = conv.clerk_user_id or user.id

    deps = SerniaDeps(
        db_session=None,  # type: ignore[arg-type]
        conversation_id=conversation_id,
        user_identifier=user_identifier,
        user_name=resolved_name,
        user_email=resolved_email,
        modality=modality,
        workspace_path=WORKSPACE_PATH,
    )
    fake_ctx = SimpleNamespace(deps=deps)

    import inspect

    sections = [{"label": "Static Instructions", "content": STATIC_INSTRUCTIONS}]
    for fn in DYNAMIC_INSTRUCTIONS:
        if inspect.iscoroutinefunction(fn):
            sections.append({"label": fn.__name__, "content": "(skipped — async side-effect only)"})
            continue
        content = fn(fake_ctx)  # type: ignore[arg-type]
        sections.append({"label": fn.__name__, "content": content or "(empty)"})

    tool_overview = await _resolve_tool_overview(deps)
    # Toolsets can also contribute system-prompt content (e.g. SkillsCapability
    # injects the skill registry). Surface those alongside the explicit
    # instructions so the preview matches what the model actually sees.
    for ts_section in tool_overview.pop("toolset_instructions", []):
        sections.append({
            "label": f"toolset:{ts_section['label']}",
            "content": ts_section["content"],
        })
    combined = "\n\n".join(s["content"] for s in sections)

    return {
        **tool_overview,
        "conversation_id": conversation_id,
        "sections": sections,
        "combined": combined,
        "model": get_model_choice(await get_active_model_key()).model_string,
        "deps": {
            "user_name": resolved_name,
            "user_email": resolved_email,
            "modality": modality,
            "is_trigger": is_trigger,
        },
    }


@router.get("/admin/settings")
async def get_admin_settings(
    user: SerniaUser = Depends(_get_sernia_user),
    session: DBSession = None,
):
    """Return current app settings including schedule config.

    On non-production environments both the triggers kill switch and the
    schedule config are reported as their effective (forced-off) values,
    not the raw DB row. PR branches inherit rows from the parent DB, so
    the raw row would otherwise surface the production config here.
    """
    from api.src.sernia_ai.triggers.scheduled_triggers import get_schedule_config
    from api.src.sernia_ai.triggers.zillow_email_event_trigger import (
        get_zillow_email_config,
    )

    available_models = [
        {
            "key": m.key,
            "label": m.label,
            "provider": m.provider,
            "cost_note": m.cost_note,
        }
        for m in AVAILABLE_MODELS
    ]

    # Zillow config is reported as-is on every env. Unlike the trigger kill
    # switch / schedule, the bypass-approval flag is only consulted inside
    # `bypass_external_email_approval`-aware code paths, which are themselves
    # gated by `is_sernia_ai_enabled` (hard-off on non-prod). Showing the real
    # value lets PR envs see what production has configured.
    zillow_email_config = await get_zillow_email_config()

    if not _IS_PRODUCTION:
        # Model selection is NOT hard-gated off on non-prod — PR envs should
        # exercise whatever model production has configured. Still read the DB
        # value (falls back to default) so the UI reflects reality.
        active_model = await get_active_model_key()
        active_effort = await get_active_thinking_effort()
        return {
            "triggers_enabled": False,
            "schedule_config": {"days_of_week": [], "hours": []},
            "model_config": {"model_key": active_model, "thinking_effort": active_effort},
            "zillow_email_config": zillow_email_config,
            "available_models": available_models,
        }

    result = await session.execute(
        select(AppSetting).where(AppSetting.key == "triggers_enabled")
    )
    row = result.scalar_one_or_none()
    schedule_config = await get_schedule_config()
    active_model = await get_active_model_key()
    active_effort = await get_active_thinking_effort()
    return {
        "triggers_enabled": row.value if row else True,
        "schedule_config": schedule_config,
        "model_config": {"model_key": active_model, "thinking_effort": active_effort},
        "zillow_email_config": zillow_email_config,
        "available_models": available_models,
    }


class _ScheduleConfigPayload(BaseModel):
    days_of_week: list[int]  # 0=Mon … 6=Sun
    hours: list[int]  # 0–23, ET


class _ModelConfigPayload(BaseModel):
    model_key: ModelKey
    thinking_effort: ThinkingEffort = DEFAULT_THINKING_EFFORT


class _ZillowEmailConfigPayload(BaseModel):
    debounce_seconds: int
    require_approval: bool


class _SettingsUpdateRequest(BaseModel):
    # `model_config` is reserved by Pydantic v2 for ConfigDict, so we store
    # the field under a different Python name and expose it as `model_config`
    # in the JSON body via alias.
    model_config = ConfigDict(populate_by_name=True)

    triggers_enabled: bool | None = None
    schedule_config: _ScheduleConfigPayload | None = None
    model_cfg: _ModelConfigPayload | None = Field(default=None, alias="model_config")
    zillow_email_config: _ZillowEmailConfigPayload | None = None


@router.patch("/admin/settings")
async def update_admin_settings(
    body: _SettingsUpdateRequest,
    user: SerniaUser = Depends(_get_sernia_user),
    session: DBSession = None,
):
    """Update app settings (upsert). Re-registers the scheduled job when schedule changes."""
    updated = {}
    if body.triggers_enabled is not None:
        stmt = pg_insert(AppSetting).values(
            key="triggers_enabled",
            value=body.triggers_enabled,
        ).on_conflict_do_update(
            index_elements=["key"],
            set_={"value": body.triggers_enabled},
        )
        await session.execute(stmt)
        await session.commit()
        updated["triggers_enabled"] = body.triggers_enabled

    if body.schedule_config is not None:
        # Validate ranges. Empty lists are allowed and mean "no scheduled
        # checks" — the scheduled job is simply unregistered in that case.
        for d in body.schedule_config.days_of_week:
            if d < 0 or d > 6:
                raise HTTPException(status_code=422, detail=f"Invalid day_of_week: {d}")
        for h in body.schedule_config.hours:
            if h < 0 or h > 23:
                raise HTTPException(status_code=422, detail=f"Invalid hour: {h}")

        config_dict = body.schedule_config.model_dump()
        stmt = pg_insert(AppSetting).values(
            key="schedule_config",
            value=config_dict,
        ).on_conflict_do_update(
            index_elements=["key"],
            set_={"value": config_dict},
        )
        await session.execute(stmt)
        await session.commit()
        updated["schedule_config"] = config_dict

        # Re-register the APScheduler job with the new config
        from api.src.sernia_ai.triggers.scheduled_triggers import apply_schedule_from_db
        await apply_schedule_from_db()

    if body.model_cfg is not None:
        # Pydantic's Literal validator already rejected unknown keys before
        # we got here — no need to re-check.
        config_dict = body.model_cfg.model_dump()
        stmt = pg_insert(AppSetting).values(
            key="model_config",
            value=config_dict,
        ).on_conflict_do_update(
            index_elements=["key"],
            set_={"value": config_dict},
        )
        await session.execute(stmt)
        await session.commit()
        updated["model_config"] = config_dict

    if body.zillow_email_config is not None:
        # Clamp debounce to a sane range. The lower bound prevents accidental
        # zero-second debounces (a trigger would fire on every email and lose
        # batching). The upper bound is the recently-fired TTL window — going
        # beyond it would let redeliveries slip past dedup.
        if body.zillow_email_config.debounce_seconds < 60:
            raise HTTPException(
                status_code=422,
                detail="debounce_seconds must be at least 60",
            )
        if body.zillow_email_config.debounce_seconds > 3600:
            raise HTTPException(
                status_code=422,
                detail="debounce_seconds must not exceed 3600 (1 hour)",
            )

        config_dict = body.zillow_email_config.model_dump()
        stmt = pg_insert(AppSetting).values(
            key="zillow_email_config",
            value=config_dict,
        ).on_conflict_do_update(
            index_elements=["key"],
            set_={"value": config_dict},
        )
        await session.execute(stmt)
        await session.commit()
        updated["zillow_email_config"] = config_dict

    return {"updated": updated}


# =============================================================================
# Helpers
# =============================================================================


async def _send_sms_reply(to_phone: str, message: str) -> None:
    """Send an SMS reply from the AI phone number, auto-splitting if long. Never raises."""
    from api.src.open_phone.service import send_message
    from api.src.sernia_ai.config import QUO_SERNIA_AI_PHONE_ID
    from api.src.sernia_ai.tools.quo_tools import split_sms

    chunks = split_sms(message)
    for chunk in chunks:
        try:
            await send_message(
                message=chunk,
                to_phone_number=to_phone,
                from_phone_number=QUO_SERNIA_AI_PHONE_ID,
            )
        except Exception:
            logfire.exception("post-approval SMS reply failed", to_phone=to_phone)
            return
    logfire.info(
        "post-approval SMS reply sent",
        to_phone=to_phone,
        parts=len(chunks),
    )


# =============================================================================
# Sub-routers
# =============================================================================

router.include_router(push_router)
