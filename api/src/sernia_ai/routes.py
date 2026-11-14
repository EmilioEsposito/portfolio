"""
Routes for the Sernia AI agent.

All routes are gated to @serniacapital.com users via a router-level dependency.
The verified Clerk User object is stashed on request.state.sernia_user by the
gate and retrieved by individual handlers via the SerniaUser dependency.
"""

import json
from typing import Literal
from uuid import uuid4

import logfire
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic_ai.ui.vercel_ai import VercelAIAdapter
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from starlette.responses import Response

from api.src.agent_conversations.auth import (
    SerniaUser,
    _display_name,
    _get_sernia_user,
    _sernia_email,
    _sernia_gate,
)
from api.src.agent_conversations.history import _merge_sms_if_needed
from api.src.agent_conversations.identity import AGENT_NAME
from api.src.agent_conversations.models import (
    get_agent_conversation,
    get_conversation_messages,
    save_agent_conversation,
)
from api.src.agent_conversations.push.routes import router as push_router
from api.src.agent_conversations.run_state import lock_conversation, require_idle
from api.src.database.app_settings import AppSetting
from api.src.database.database import DBSession
from api.src.sernia_ai.chat_runs import (
    ChatRun,
    cancel_run,
    new_run,
)
from api.src.sernia_ai.config import WORKSPACE_PATH
from api.src.sernia_ai.deps import SerniaDeps
from api.src.sernia_ai.general_agent import sernia_agent
from api.src.sernia_ai.model_config import (
    AVAILABLE_MODELS,
    ModelKey,
    get_active_model_key,
    get_active_thinking_effort,
    get_model_choice,
)
from api.src.sernia_ai.triggers.ai_sms_event_trigger import (
    _sanitize_tool_calls,
)
from api.src.utils.automation import _IS_PRODUCTION
from api.src.utils.chat_attachments import read_chat_request
from api.src.utils.openrouter_models import DEFAULT_THINKING_EFFORT, ThinkingEffort
from api.src.zillow_leads.identity import is_lead_conversation
from api.src.zillow_leads.settings import router as zillow_leads_router

router = APIRouter(
    prefix="/sernia-ai",
    tags=["sernia-ai"],
    dependencies=[Depends(_sernia_gate)],
)


# =============================================================================
# Streaming Chat
# =============================================================================


@router.post("/chat", response_class=Response, summary="Chat with Sernia AI assistant")
async def chat_sernia(
    request: Request,
    user: SerniaUser = Depends(_get_sernia_user),
    session: DBSession = None,
) -> Response:
    """Accept and save a turn before starting a server-owned, disconnect-safe run."""
    body = await read_chat_request(request)
    conversation_id = body.get("id") or str(uuid4())
    request_id = body.get("request_id") or str(uuid4())
    if any(
        not isinstance(value, str) or not 1 <= len(value) <= 200
        for value in (conversation_id, request_id)
    ):
        raise HTTPException(400, "Invalid conversation or request ID")
    messages = body.get("messages", [])
    last_message = messages[-1] if messages else None
    if not last_message or last_message.get("role") != "user":
        raise HTTPException(400, "A new user message is required")
    if not any(
        part.get("type") == "file"
        or (
            part.get("type") == "text"
            and isinstance(part.get("text"), str)
            and part["text"].strip()
        )
        for part in last_message.get("parts", [])
    ):
        raise HTTPException(400, "Enter a message or attach a file")
    try:
        run_input = VercelAIAdapter.build_run_input(
            json.dumps(
                {
                    **body,
                    "id": conversation_id,
                    "messages": [last_message],
                }
            ).encode()
        )
        adapter = VercelAIAdapter(agent=sernia_agent, run_input=run_input)
        accepted_messages = adapter.sanitize_messages(adapter.messages)
    except (ValidationError, ValueError):
        raise HTTPException(422, "Invalid chat message") from None

    await lock_conversation(session, conversation_id)
    existing = await get_agent_conversation(session, conversation_id, retries=1)
    if existing:
        if existing.agent_name != AGENT_NAME:
            raise HTTPException(404, "Conversation not found")
        if is_lead_conversation(existing.metadata_):
            raise HTTPException(
                409,
                "This conversation belongs to the Zillow lead agent. Approve or reject its draft, or start a new chat.",
            )
        require_idle(existing.metadata_)
        if (existing.metadata_ or {}).get("web_chat_run", {}).get("id") == request_id:
            raise HTTPException(
                409, "This request was already accepted. Reopen the conversation to see its result."
            )

    history = await get_conversation_messages(
        conversation_id,
        session=session,
        include_terminal=True,
        retries=1,
    )
    history = _sanitize_tool_calls(await _merge_sms_if_needed(conversation_id, history))
    run = new_run(request_id)
    await save_agent_conversation(
        session,
        conversation_id,
        AGENT_NAME,
        [*history, *accepted_messages],
        clerk_user_id=user.id,
        metadata={"web_chat_run": run},
    )
    logfire.info(
        "sernia chat accepted",
        conversation_id=conversation_id,
        request_id=request_id,
        clerk_user_id=user.id,
        user_name=_display_name(user),
        user_email=_sernia_email(user),
        db_msg_count=len(history),
        _tags=["trigger:user"],
    )
    deps = SerniaDeps(
        db_session=session,
        conversation_id=conversation_id,
        user_identifier=user.id,
        user_name=_display_name(user),
        user_email=_sernia_email(user),
        modality="web_chat",
        workspace_path=WORKSPACE_PATH,
    )
    execution = ChatRun(adapter, history, deps, run)
    execution.start()
    response = adapter.streaming_response(execution.events())
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Cache-Control"] = "no-cache, no-transform"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Conversation-Id"] = conversation_id
    return response


class CancelChatRequest(BaseModel):
    request_id: str = Field(min_length=1, max_length=200)


@router.post("/conversation/{conversation_id}/cancel")
async def cancel_conversation_run(
    conversation_id: str,
    body: CancelChatRequest,
    user: SerniaUser = Depends(_get_sernia_user),
):
    return {"run": await cancel_run(conversation_id, body.request_id)}


# =============================================================================
# Admin
# =============================================================================


async def _skills_preview() -> tuple[str, list[dict]]:
    """What the skills capabilities add to a run: the catalog and the load tool.

    pydantic-ai builds both when a run starts, so this runs a throwaway agent
    that has only those two capabilities against a local model that records
    what it was sent. No provider is called and no tool runs.
    """
    from pydantic_ai import Agent
    from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    from api.src.sernia_ai.general_agent import lead_policy_capability, workspace_skills

    seen: dict = {}

    def record(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen["instructions"] = info.instructions or ""
        seen["tools"] = [
            {
                "name": td.name,
                "description": td.description or "",
                "parameters_json_schema": td.parameters_json_schema,
                "kind": getattr(td, "kind", None),
                "metadata": getattr(td, "metadata", None) or {},
            }
            for td in info.function_tools
        ]
        return ModelResponse(parts=[TextPart("ok")])

    probe = Agent(
        FunctionModel(record),
        deps_type=SerniaDeps,
        capabilities=[workspace_skills, lead_policy_capability],
        name="sernia_skills_preview",
    )
    await probe.run("preview", deps=None)  # type: ignore[arg-type]
    return seen.get("instructions", ""), seen.get("tools", [])


async def _resolve_tool_overview(deps: SerniaDeps) -> dict:
    """Walk the agent's toolsets and return tool definitions as the model sees them.

    Uses ``Toolset.get_tools(ctx)`` — the same call PydanticAI makes when
    packaging tools for the model — so the preview is bit-for-bit what gets
    injected (native web search/fetch tools are listed separately below).
    """
    from pydantic_ai import RunContext
    from pydantic_ai.usage import RunUsage

    ctx: RunContext[SerniaDeps] = RunContext(  # type: ignore[call-arg]
        deps=deps, model=None, usage=RunUsage()
    )

    # Each toolset's own ``get_instructions(ctx)`` is also called, so anything a
    # toolset adds to the system prompt shows in the Context tab. The skill
    # catalog is not one of those: capabilities add it, see `_skills_preview`.
    import inspect

    def _label_for(ts) -> str:
        explicit = getattr(ts, "name", None)
        if explicit:
            return explicit
        cls_name = type(ts).__name__
        if cls_name == "_AgentFunctionToolset":
            return "agent.tool registrations"
        return cls_name

    toolsets_out: list[dict] = []
    toolset_instructions: list[dict] = []
    total = 0
    from pydantic_ai.toolsets import CombinedToolset

    from api.src.sernia_ai.general_agent import workspace_capability

    # The workspace file tools come from a capability. `sernia_agent.toolsets`
    # also holds the capabilities' tools in one unnamed CombinedToolset, which
    # only lists its tools inside a run, so it is skipped and the workspace
    # tools are listed from the capability instead.
    named_toolsets = [
        ts
        for ts in sernia_agent.toolsets
        if not (isinstance(ts, CombinedToolset) and not getattr(ts, "name", None))
    ]
    for ts in [*named_toolsets, workspace_capability.get_toolset()]:
        try:
            tools = await ts.get_tools(ctx)
        except Exception as e:
            toolsets_out.append(
                {
                    "name": getattr(ts, "name", None) or type(ts).__name__,
                    "error": f"{type(e).__name__}: {e}",
                    "tools": [],
                }
            )
            continue
        label = _label_for(ts)
        entries = []
        for t in tools.values():
            td = t.tool_def
            entries.append(
                {
                    "name": td.name,
                    "description": td.description or "",
                    "parameters_json_schema": td.parameters_json_schema,
                    "kind": getattr(td, "kind", None),
                    "metadata": getattr(td, "metadata", None) or {},
                }
            )
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

    # Skills: the catalog the capabilities put in the instructions, and the
    # `load_capability` tool the model loads one with.
    try:
        skills_catalog, skills_tools = await _skills_preview()
    except Exception as e:
        toolsets_out.append({"name": "skills", "error": f"{type(e).__name__}: {e}", "tools": []})
    else:
        if skills_tools:
            total += len(skills_tools)
            toolsets_out.append({"name": "skills", "tools": skills_tools})
        if skills_catalog:
            toolset_instructions.append({"label": "skills", "content": skills_catalog})

    # Native tools (web search/fetch) contributed by the WebSearch/WebFetch
    # capabilities. The agent collects them on ``_cap_native_tools`` at
    # construction; which ones actually reach the model is resolved per run
    # based on provider support (e.g. WebFetchTool is dropped on OpenRouter).
    builtins: list[dict] = []
    seen: set[str] = set()
    base_builtins = getattr(sernia_agent, "_cap_native_tools", []) or []
    for bt in list(base_builtins):
        kind = getattr(bt, "kind", type(bt).__name__)
        if kind in seen:
            continue
        seen.add(kind)
        builtins.append(
            {
                "name": kind,
                "type": type(bt).__name__,
                "config": {
                    k: v
                    for k, v in (bt.__dict__ if hasattr(bt, "__dict__") else {}).items()
                    if not k.startswith("_") and not callable(v)
                },
            }
        )

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

    from api.src.sernia_ai.instructions import DYNAMIC_INSTRUCTIONS, STATIC_INSTRUCTIONS

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
    # Toolsets and capabilities can also contribute system-prompt content
    # (e.g. the skill catalog). Surface those alongside the explicit
    # instructions so the preview matches what the model actually sees.
    for ts_section in tool_overview.pop("toolset_instructions", []):
        sections.append(
            {
                "label": f"toolset:{ts_section['label']}",
                "content": ts_section["content"],
            }
        )
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

    from api.src.agent_conversations.identity import TRIGGER_BOT_ID, TRIGGER_BOT_NAME
    from api.src.google.common.config import GOOGLE_DELEGATION_EMAIL
    from api.src.sernia_ai.instructions import DYNAMIC_INSTRUCTIONS, STATIC_INSTRUCTIONS

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
    # Toolsets and capabilities can also contribute system-prompt content
    # (e.g. the skill catalog). Surface those alongside the explicit
    # instructions so the preview matches what the model actually sees.
    for ts_section in tool_overview.pop("toolset_instructions", []):
        sections.append(
            {
                "label": f"toolset:{ts_section['label']}",
                "content": ts_section["content"],
            }
        )
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
    from api.src.zillow_leads.queue import (
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

    # Compatibility response for older clients. Dedicated lead settings live
    # at /zillow-leads/settings; the old approval flag does not enable sends.
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

    result = await session.execute(select(AppSetting).where(AppSetting.key == "triggers_enabled"))
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
    if body.zillow_email_config is not None:
        raise HTTPException(
            status_code=410,
            detail="Zillow settings moved to /api/zillow-leads/settings.",
        )
    updated = {}
    if body.triggers_enabled is not None:
        stmt = (
            pg_insert(AppSetting)
            .values(
                key="triggers_enabled",
                value=body.triggers_enabled,
            )
            .on_conflict_do_update(
                index_elements=["key"],
                set_={"value": body.triggers_enabled},
            )
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
        stmt = (
            pg_insert(AppSetting)
            .values(
                key="schedule_config",
                value=config_dict,
            )
            .on_conflict_do_update(
                index_elements=["key"],
                set_={"value": config_dict},
            )
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
        stmt = (
            pg_insert(AppSetting)
            .values(
                key="model_config",
                value=config_dict,
            )
            .on_conflict_do_update(
                index_elements=["key"],
                set_={"value": config_dict},
            )
        )
        await session.execute(stmt)
        await session.commit()
        updated["model_config"] = config_dict

    return {"updated": updated}


# =============================================================================
# Sub-routers
# =============================================================================

router.include_router(push_router)
router.include_router(zillow_leads_router)
