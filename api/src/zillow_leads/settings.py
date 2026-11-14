"""Settings owned by the dedicated Zillow Leads Agent.

The retired general-agent flag must never enable sends in this agent. Only an
explicit write to SETTINGS_KEY can turn off approval. The existing queue's
batching window is preserved when the new setting has not been saved yet.
"""

import logfire
from clerk_backend_api import User
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.database.app_settings import AppSetting
from api.src.database.database import DBSession
from api.src.utils.automation import is_sernia_ai_enabled
from api.src.utils.clerk import verify_serniacapital_user
from api.src.zillow_leads.config import (
    DEFAULT_ZILLOW_DEBOUNCE_SECONDS,
    DEFAULT_ZILLOW_REQUIRE_APPROVAL,
)

SETTINGS_KEY = "zillow_lead_config"


class LeadSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    debounce_seconds: int = Field(default=DEFAULT_ZILLOW_DEBOUNCE_SECONDS, ge=60, le=3600)
    require_approval: StrictBool = DEFAULT_ZILLOW_REQUIRE_APPROVAL


async def read_settings(session: AsyncSession) -> LeadSettings:
    value = await session.scalar(select(AppSetting.value).where(AppSetting.key == SETTINGS_KEY))
    if value is not None:
        return LeadSettings.model_validate(value)
    legacy = await session.scalar(
        select(AppSetting.value).where(AppSetting.key == "zillow_email_config")
    )
    if isinstance(legacy, dict):
        return LeadSettings(
            debounce_seconds=legacy.get("debounce_seconds", DEFAULT_ZILLOW_DEBOUNCE_SECONDS)
        )
    return LeadSettings()


async def effective_settings() -> LeadSettings:
    """Runtime failures keep approval on. The settings API surfaces errors."""
    from api.src.database.database import AsyncSessionFactory

    try:
        async with AsyncSessionFactory() as session:
            return await read_settings(session)
    except Exception:
        logfire.exception("zillow lead: settings unavailable, requiring approval")
        return LeadSettings()


class SettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    settings: LeadSettings
    based_on: LeadSettings


router = APIRouter(prefix="/zillow-leads", dependencies=[Depends(verify_serniacapital_user)])


class SettingsView(LeadSettings):
    automation_enabled: bool


@router.get("/settings", response_model=SettingsView)
async def get_settings(session: DBSession):
    try:
        settings = await read_settings(session)
        return SettingsView(
            **settings.model_dump(), automation_enabled=await is_sernia_ai_enabled()
        )
    except Exception:
        logfire.exception("zillow lead: settings could not be loaded")
        raise HTTPException(
            status_code=503, detail="Settings could not be loaded. Try again."
        ) from None


@router.patch("/settings", response_model=LeadSettings)
async def update_settings(
    body: SettingsUpdate, session: DBSession, user: User = Depends(verify_serniacapital_user)
):
    # Create the row if absent, then lock it. Concurrent first saves must also
    # compare against the winner's value instead of silently overwriting it.
    current = await read_settings(session)
    await session.execute(
        insert(AppSetting)
        .values(key=SETTINGS_KEY, value=current.model_dump())
        .on_conflict_do_nothing(index_elements=["key"])
    )
    row = await session.scalar(
        select(AppSetting).where(AppSetting.key == SETTINGS_KEY).with_for_update()
    )
    if LeadSettings.model_validate(row.value) != body.based_on:
        raise HTTPException(
            status_code=409, detail="Settings changed elsewhere. Reload before saving."
        )
    row.value = body.settings.model_dump()
    await session.commit()
    logfire.info("zillow lead: settings updated", user_id=user.id, **body.settings.model_dump())
    return body.settings
