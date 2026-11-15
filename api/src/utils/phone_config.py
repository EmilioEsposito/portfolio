"""Phone numbers that runtime code reads from configuration.

This repository is public, and phone numbers written in it are easy to harvest
for spam. Runtime code therefore reads Sernia's numbers from environment
variables, and tests use fictional numbers in the reserved 555-0100 to 555-0199
range. A required number that is missing stops the caller with an error naming
the variable, so nothing renders or dials an empty number.
"""

import os
import re

TEAM_PHONE_ENV = "SERNIA_TEAM_PHONE"

_US_E164 = re.compile(r"\+1[2-9]\d{2}[2-9]\d{6}")
_E164 = re.compile(r"\+[1-9]\d{7,14}")


class PhoneConfigError(RuntimeError):
    """A configured phone number is missing or malformed.

    The message names the variable and never repeats its value.
    """


def required_us_phone(name: str) -> str:
    """The US number in environment variable ``name``, as E.164 (``+14125550100``)."""
    value = os.environ.get(name, "").strip()
    if not value:
        raise PhoneConfigError(
            f"{name} is not set. Set it to the number in E.164 form, like +14125550100."
        )
    if not _US_E164.fullmatch(value):
        raise PhoneConfigError(f"{name} must be a US number in E.164 form, like +14125550100.")
    return value


def optional_phone_list(name: str) -> list[str]:
    """The comma-separated E.164 numbers in environment variable ``name``; empty when unset."""
    numbers = [part.strip() for part in os.environ.get(name, "").split(",") if part.strip()]
    if any(not _E164.fullmatch(number) for number in numbers):
        raise PhoneConfigError(f"{name} must list numbers in E.164 form, separated by commas.")
    return numbers


def us_phone_display(e164: str) -> str:
    """``+14125550100`` written as ``412-555-0100``, the way Sernia prints its numbers."""
    digits = e164.removeprefix("+1")
    return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"


def team_phone() -> str:
    """The Sernia Capital Team line as E.164. Leads see it, and so do email signatures."""
    return required_us_phone(TEAM_PHONE_ENV)
