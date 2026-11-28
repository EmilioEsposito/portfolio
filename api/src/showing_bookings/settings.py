"""The booking settings, kept in ``app_settings`` under ``showing_bookings_config``.

- ``send_mode``: what a booking's texts, emails and calendar events do (``guard.py``).
  "test" sends only to the test list (``SHOWING_TEST_PHONES`` and
  ``SHOWING_TEST_EMAILS``) and captures the rest. "capture" sends nothing: each
  message is rendered and kept until a person sends it (``captured.py``). "live"
  sends to real people, in production, for the units that are live.
- ``live_units``: the units that are live, as their listings spell them.
- ``live_all_units``: every unit is live, including ones added later.
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

In live mode the public and the lead agent are offered only the units that are
live (``unit_open``).

A row saved before ``send_mode`` had a test mode reads as what it meant: "off"
is "capture"; "live" with test sends on and no live units is "test"; any other
row keeps its mode. The dropped key, ``test_sends_enabled``, is ignored.

A preview database is a copy of production's rows, so whatever this row says,
outside production the guard sends to nobody but the test list.
"""

import os
from typing import Any

import logfire
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator
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
    live_all_units: StrictBool = False
    lead_agent_uses_open_times: StrictBool = False
    # None: the environment's default (``public_booking_on``).
    public_booking_enabled: StrictBool | None = None
    public_limits: PublicLimits = Field(default_factory=PublicLimits)

    @model_validator(mode="before")
    @classmethod
    def _read_an_old_row(cls, value: Any) -> Any:
        """A row from before test mode, as what it meant then (the module's docstring)."""
        if not isinstance(value, dict):
            return value
        value = dict(value)
        test_sends = value.pop("test_sends_enabled", False)
        mode = value.get("send_mode")
        if mode == "off":
            value["send_mode"] = "capture"
        elif mode == "live" and test_sends is True and not value.get("live_units"):
            value["send_mode"] = "test"
        return value

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


def unit_is_live(settings: ShowingBookingSettings, unit: str) -> bool:
    """Whether the settings tick ``unit`` for live mode, whatever the mode is now."""
    return settings.live_all_units or unit.strip().casefold() in {
        live.casefold() for live in settings.live_units
    }


def unit_open(settings: ShowingBookingSettings, unit: str) -> bool:
    """Whether the public and the lead agent may book ``unit``.

    In live mode only a unit that is live, since someone booked into another
    would get no text at all. In test and capture mode, every unit on offer.
    The team's own actions, existing showings and their links never ask this.
    """
    return settings.send_mode != "live" or unit_is_live(settings, unit)


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


def public_booking_on(settings: ShowingBookingSettings, environment: str) -> bool:
    """Unset means off in production and on everywhere else."""
    if settings.public_booking_enabled is None:
        return environment != "production"
    return settings.public_booking_enabled


def current_environment() -> str:
    """Railway's environment name, read at call time; empty when running locally."""
    return os.getenv(ENVIRONMENT_ENV, "")


def _env_list(name: str) -> list[str]:
    return [item.strip() for item in os.getenv(name, "").split(",") if item.strip()]


def read_test_recipients() -> frozenset[str]:
    """The test list: phone numbers (E.164) and email addresses, read at call time.

    An entry that is not a valid US mobile number or email address is
    ignored, so a typo can only narrow who receives test sends.
    """
    values = [normalize_us_mobile(item) for item in _env_list(TEST_PHONES_ENV)]
    values += [normalize_email(item) for item in _env_list(TEST_EMAILS_ENV)]
    unusable = sum(value is None for value in values)
    if unusable:
        logfire.warn("showing bookings: unusable test recipients ignored", count=unusable)
    return frozenset(value for value in values if value)
