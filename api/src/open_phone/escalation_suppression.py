"""Private, deterministic sender/topic exceptions for automatic notifications."""

import os
import re
import unicodedata

import logfire
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator

VARIABLE = "ESCALATION_SUPPRESSION_RULES"


def normalize_number(value: str) -> str:
    """Accept E.164 and common US display formats; reject ambiguous identities."""
    if not re.fullmatch(r"\+?[0-9() .-]+", value.strip()):
        return ""
    digits = re.sub(r"\D", "", value)
    if len(digits) == 10 and not value.strip().startswith("+"):
        digits = "1" + digits
    if not 11 <= len(digits) <= 15 or digits.startswith("0"):
        return ""
    return "+" + digits


def normalize_text(value: str) -> str:
    return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", value).casefold()))


class SuppressionRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_numbers: list[str] = Field(min_length=1, max_length=20)
    match_terms: list[str] = Field(min_length=1, max_length=100)
    bypass_terms: list[str] = Field(min_length=1, max_length=100)

    @field_validator("from_numbers")
    @classmethod
    def valid_numbers(cls, values: list[str]) -> list[str]:
        normalized = [normalize_number(value) for value in values]
        if not all(normalized):
            raise ValueError("Invalid sender identity")
        return normalized

    @field_validator("match_terms", "bypass_terms")
    @classmethod
    def valid_terms(cls, values: list[str]) -> list[str]:
        normalized = [normalize_text(value) for value in values]
        if not all(normalized) or any(len(value) > 200 for value in normalized):
            raise ValueError("Invalid matching term")
        return normalized


def should_suppress(event: dict) -> bool:
    """Match only the current sender and message, never quoted thread history.

    Bypass terms take precedence across all matching sender rules. They resume
    normal assessment, not an unconditional call. Configuration errors surface
    as fixed diagnostics so private values cannot leak through validation logs.
    """
    raw = os.getenv(VARIABLE, "").strip()
    if not raw:
        return False
    try:
        rules = TypeAdapter(list[SuppressionRule]).validate_json(raw)
    except ValidationError:
        raise ValueError(f"Invalid {VARIABLE}; values withheld") from None
    sender = normalize_number(event.get("from_number") or "")
    text = " " + normalize_text(event.get("message_text") or "") + " "
    matching = [rule for rule in rules if sender in rule.from_numbers]
    if any(f" {term} " in text for rule in matching for term in rule.bypass_terms):
        return False
    suppressed = any(f" {term} " in text for rule in matching for term in rule.match_terms)
    if suppressed:
        logfire.info("Automatic escalation suppressed by configured sender/topic rule")
    return suppressed
