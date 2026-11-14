"""Legacy SMS history enrichment shared by chat admission and conversation reads.

Normal conversation reads use stored messages only. The general agent's Quo
adapter is loaded on demand for the existing SMS conversation ID prefix.
"""

import logfire

_SMS_CONV_PREFIX = "ai_sms_from_"


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

    from api.src.sernia_ai.triggers.ai_sms_event_trigger import (
        _fetch_sms_thread,
        _merge_sms_into_history,
    )

    digits = conversation_id[len(_SMS_CONV_PREFIX) :]
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
