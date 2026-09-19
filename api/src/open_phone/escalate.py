import os
import re  # Added for normalization function

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(".env"), override=True)
import json
import random

# from twilio.rest import Client # removed to reduce bundle size
import httpx
import logfire
from fastapi import HTTPException

from api.src.contact.service import get_contact_by_slug
from api.src.open_phone.escalation_assessment import assess_state
from api.src.open_phone.escalation_context import (
    add_history_timing,
    event_time,
    fetch_escalation_history,
)
from api.src.open_phone.escalation_policy import (
    ESCALATION_QUESTION as ESCALATION_QUESTION,
)
from api.src.open_phone.escalation_policy import (
    ai_instructions as ai_instructions,
)
from api.src.open_phone.escalation_policy import (
    explicit_keywords,
)

# --- Twilio Configuration ---
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_FLOW_ID = "FW708fd372ad2ccc709cdaf1565f087bfa"

if not TWILIO_ACCOUNT_SID:
    raise HTTPException(status_code=500, detail="TWILIO_ACCOUNT_SID is missing")
if not TWILIO_AUTH_TOKEN:
    raise HTTPException(status_code=500, detail="TWILIO_AUTH_TOKEN is missing")

# twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN) # removed to reduce bundle size


# Explicit keywords Escalation

negation_prefixes = [
    "notan",
    "nota",
    "not",
    "non",
]

# sort keywords by length, longest first
explicit_keywords.sort(key=len, reverse=True)
negation_phases = [
    negation_prefix + explicit_keyword
    for negation_prefix in negation_prefixes
    for explicit_keyword in explicit_keywords
]


never_escalate_from_numbers = [
    "+16266125747",
]

# Caller ID for escalation calls — tenants/team recognize this Twilio number,
# and Emilio's/Peppino's phones allow it through Do Not Disturb.
ESCALATE_FROM_NUMBER = "+14129001989"

# Default escalation recipients, resolved from the contacts DB by slug.
DEFAULT_ESCALATION_CONTACT_SLUGS = ["emilio", "peppino"]


# --- Normalization function ---
def normalize_text_for_keyword_search(text: str) -> str:
    if not text:
        return ""
    # Lowercase and remove all non-alphanumeric characters
    text = re.sub(r"[^a-z0-9]", "", text.lower())
    # remove negation phrases
    for negation_phrase in negation_phases:
        text = text.replace(negation_phrase, "")
    return text


async def ai_assess_for_escalation(
    open_phone_event: dict, max_retries: int = 1, *, mode: str | None = None
) -> tuple[bool, str]:
    """Fetch context once, assess without side effects, then return one dispatch decision."""
    history = await fetch_escalation_history(open_phone_event)
    state = add_history_timing(
        {
            "message_text": open_phone_event.get("message_text"),
            "timestamp": event_time(open_phone_event["event_timestamp"]).isoformat(),
            "timezone": "America/New_York",
            "prior_messages": [message.model_dump() for message in history.messages],
            "history_status": history.status,
        }
    )
    # Production defaults to Luna. Running both is an explicit operational choice.
    selected_mode = mode if mode is not None else os.getenv("ESCALATION_MODEL_MODE", "luna")
    decision = await assess_state(state, mode=selected_mode, max_retries=max_retries)
    return decision.should_escalate, decision.reason


async def resolve_escalation_numbers(escalate_to_numbers: list[str] | None = None) -> list[str]:
    """Return the given numbers, or look up the default escalation contacts from the DB."""
    if escalate_to_numbers:
        return escalate_to_numbers
    numbers: list[str] = []
    for slug in DEFAULT_ESCALATION_CONTACT_SLUGS:
        contact = await get_contact_by_slug(slug)
        if contact and contact.phone_number:
            numbers.append(contact.phone_number)
        else:
            logfire.error(f"Escalation contact '{slug}' not found or has no phone number")
    return numbers


@logfire.instrument()
async def trigger_twilio_escalation(
    message_text: str,
    escalate_to_numbers: list[str] | None = None,
    mock: bool = False,
) -> int:
    """
    Trigger the Twilio Studio Flow escalation — a call from ESCALATE_FROM_NUMBER
    (which bypasses Do Not Disturb) to each escalation number.

    Returns the number of successful flow executions.
    """
    escalate_to_numbers = await resolve_escalation_numbers(escalate_to_numbers)
    if not escalate_to_numbers:
        logfire.error("No escalation contacts found, cannot escalate")
        return 0

    successful_escalations = 0
    incident_id = random.randint(100, 999)

    # Add incident ID to the message text
    if message_text:
        message_text += f"\nIncident ID: {incident_id}"
    else:
        message_text = f"Escalation Triggered\nIncident ID: {incident_id}"

    logfire.info(
        f"Escalation triggered. INCIDENT_ID: {incident_id} to numbers {escalate_to_numbers}"
    )
    # Construct the API URL
    studio_api_url = f"https://studio.twilio.com/v2/Flows/{TWILIO_FLOW_ID}/Executions"

    # Failures are isolated per recipient: one failed execution must not
    # suppress the escalation calls to everyone after it.
    async with httpx.AsyncClient(
        auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN), timeout=30
    ) as client:
        for escalate_to_number in escalate_to_numbers:
            # Prepare the payload
            payload = {
                "To": escalate_to_number,
                "From": ESCALATE_FROM_NUMBER,
                "Parameters": json.dumps(
                    {"message_text": message_text}
                ),  # Parameters must be a JSON string
            }

            try:
                if mock:
                    result_message = f"Mocking Twilio escalation to {escalate_to_number} with message: {message_text}"
                    logfire.info(result_message)
                    successful_escalations += 1
                else:
                    response = await client.post(studio_api_url, data=payload)
                    response.raise_for_status()  # Raise an exception for bad status codes (4xx or 5xx)

                    execution_data = response.json()
                    execution_sid = execution_data.get("sid")
                    result_message = f"Successfully created Twilio execution: {execution_sid} for INCIDENT_ID {incident_id}"
                    logfire.info(result_message)
                    successful_escalations += 1
            except httpx.HTTPError as e:
                # Log the error, including the response text if available
                error_message = f"Failed to create Twilio execution to {escalate_to_number} for INCIDENT_ID {incident_id}: {str(e)}"
                response = getattr(e, "response", None)
                if response is not None:
                    error_message += f"\nResponse status: {response.status_code}"
                    error_message += f"\nResponse text: {response.text}"
                logfire.exception(error_message)  # exc_info=True adds traceback
            except Exception as e:
                # Catch any other unexpected errors during the process
                logfire.exception(
                    f"An unexpected error occurred during Twilio escalation to {escalate_to_number} for INCIDENT_ID {incident_id}: {str(e)}"
                )

    return successful_escalations


@logfire.instrument()
async def analyze_for_twilio_escalation(
    open_phone_event: dict, escalate_to_numbers: list[str] = None, mock: bool = False
):
    """
    Analyzes an OpenPhone event and potentially triggers a Twilio Studio Flow execution.
    """

    should_escalate = False  # default to false

    event_from_number = open_phone_event.get("from_number")
    event_message_text = open_phone_event.get("message_text")
    event_id = open_phone_event.get("event_id", "")
    logfire.info(f"AI Assessment: Analyzing for Twilio escalation. OpenPhone event_id: {event_id}")

    # # 320-09 Escalation between 8pm and 7am
    # unit32009_numbers = ["+14124786168", "+14122280772"]
    # if event_from_number in unit32009_numbers and (now_et.hour >= 20 or now_et.hour <= 7):
    #     should_escalate = True
    #     escalate_to_numbers = ["+14126800593"] # Specific target for 320-09
    #     escalate_from_number = "+14129001989" # Specific sender for 320-09

    # Assess with the configured model(s); notification dispatch happens once below
    try:
        should_escalate, reason = await ai_assess_for_escalation(open_phone_event)
        logfire.info(
            f"AI escalation assessment: should_escalate={should_escalate}, reason={reason}"
        )
    except Exception as e:
        logfire.error(f"AI Error assessing for escalation: {e}")

    # Keyword fallback disabled — was too noisy with false positives
    # if not should_escalate and any(
    #     keyword in normalize_text_for_keyword_search(event_message_text)
    #     for keyword in explicit_keywords
    # ):
    #     should_escalate = True
    #     reason = f"Keyword fallback escalation triggered for event_id={event_id}"
    #     logfire.info(f"Explicit keyword escalation triggered. event_id={event_id} message_text={event_message_text}")

    event_message_text = (
        f"URGENT! {event_from_number} said: {event_message_text}"  # Prepend identifier
    )

    # override should_escalate if the from number is in the never_escalate_from_numbers list
    if should_escalate and event_from_number in never_escalate_from_numbers:
        should_escalate = False
        logfire.info(
            f"Event from number {event_from_number} is in the never_escalate_from_numbers list, so not escalating"
        )

    if not should_escalate:
        logfire.debug(
            f"Event {open_phone_event.get('event_id')} (type: {open_phone_event.get('event_type')}) did not meet Twilio escalation criteria."
        )
        return 0

    logfire.info(f"Escalation triggered for EVENT_ID {event_id}")
    return await trigger_twilio_escalation(
        event_message_text, escalate_to_numbers=escalate_to_numbers, mock=mock
    )
