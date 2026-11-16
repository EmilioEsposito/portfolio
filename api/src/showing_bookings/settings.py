"""The send mode setting, kept in ``app_settings`` under ``showing_bookings_config``.

- ``send_mode``: "off" skips every notice, "capture" renders and keeps it
  without calling a provider, "live" lets the guard (``guard.py``) send it.
- ``live_units``: the units whose notices really go out in production.
- ``test_sends_enabled``: in live mode, also send notices addressed only to
  the test recipients in ``SHOWING_TEST_PHONES`` and ``SHOWING_TEST_EMAILS``.
- ``lead_agent_uses_open_times``: the Zillow lead agent offers the open times
  computed here (``lead_tours.py``) instead of none. Off by default, and a row
  that cannot be read counts as off.
- ``public_booking_enabled``: the public booking routes answer
  (``public_routes.py``). True, false or unset; unset means off in production
  and on everywhere else (``public_booking_on``). A row that cannot be read
  counts as unset.
- ``public_limits``: how many bookings one mobile, one email and one IP
  address may make (``PublicLimits``). Kept here rather than in the repo,
  which is public.

A preview database is a copy of production's rows, so whatever this row says,
outside production the guard sends nothing but test sends.
"""

import os

import logfire
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.database.app_settings import AppSetting
from api.src.showing_bookings.schema import SendMode, normalize_email, normalize_us_mobile

SETTINGS_KEY = "showing_bookings_config"
TEST_PHONES_ENV = "SHOWING_TEST_PHONES"
TEST_EMAILS_ENV = "SHOWING_TEST_EMAILS"
ENVIRONMENT_ENV = "RAILWAY_ENVIRONMENT_NAME"


class PublicLimits(BaseModel):
    """Limits on public bookings. A booking past one is refused with the field named."""

    model_config = ConfigDict(extra="forbid")

    # Booked showings still to come for one mobile number, and for one email.
    upcoming_per_mobile: int = Field(default=2, ge=1, le=20)
    upcoming_per_email: int = Field(default=2, ge=1, le=20)
    # Bookings (withdrawn ones included) from one IP address in 24 hours.
    bookings_per_ip_per_day: int = Field(default=6, ge=1, le=100)
    # Page bookings from everyone together in 24 hours, withdrawn ones included:
    # a cap on how many strangers a run past Turnstile could have texted.
    bookings_per_day: int = Field(default=50, ge=1, le=1000)


class ShowingBookingSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    send_mode: SendMode = "capture"
    live_units: list[str] = Field(default_factory=list)
    test_sends_enabled: StrictBool = False
    lead_agent_uses_open_times: StrictBool = False
    # None: the environment's default (``public_booking_on``).
    public_booking_enabled: StrictBool | None = None
    public_limits: PublicLimits = Field(default_factory=PublicLimits)

    @field_validator("live_units")
    @classmethod
    def _clean_units(cls, units: list[str]) -> list[str]:
        cleaned: list[str] = []
        for unit in units:
            unit = unit.strip()
            if not unit:
                raise ValueError("A live unit cannot be blank.")
            if unit.casefold() not in {seen.casefold() for seen in cleaned}:
                cleaned.append(unit)
        return cleaned


async def read_settings(session: AsyncSession) -> ShowingBookingSettings:
    """The stored settings, or the defaults (capture, nothing live) when none are saved."""
    value = await session.scalar(select(AppSetting.value).where(AppSetting.key == SETTINGS_KEY))
    return (
        ShowingBookingSettings() if value is None else ShowingBookingSettings.model_validate(value)
    )


async def settings_for_delivery(session: AsyncSession) -> ShowingBookingSettings:
    """The settings the outbox delivers under. A row that cannot be read means capture."""
    try:
        return await read_settings(session)
    except Exception:
        logfire.exception("showing bookings: settings unreadable, capturing")
        return ShowingBookingSettings()


def public_booking_default(environment: str) -> bool:
    """Unset means off in production and on everywhere else."""
    return environment != "production"


def public_booking_on(settings: ShowingBookingSettings, environment: str) -> bool:
    if settings.public_booking_enabled is None:
        return public_booking_default(environment)
    return settings.public_booking_enabled


def current_environment() -> str:
    """Railway's environment name, read at call time; empty when running locally."""
    return os.getenv(ENVIRONMENT_ENV, "")


def _env_list(name: str) -> list[str]:
    return [item.strip() for item in os.getenv(name, "").split(",") if item.strip()]


def read_test_recipients() -> frozenset[str]:
    """The test phone numbers (E.164) and email addresses, read at call time.

    An entry that is not a valid US mobile number or email address is
    ignored, so a typo can only narrow who receives test sends.
    """
    values = [normalize_us_mobile(item) for item in _env_list(TEST_PHONES_ENV)]
    values += [normalize_email(item) for item in _env_list(TEST_EMAILS_ENV)]
    unusable = sum(value is None for value in values)
    if unusable:
        logfire.warn("showing bookings: unusable test recipients ignored", count=unusable)
    return frozenset(value for value in values if value)
