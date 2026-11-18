"""
FastAPI routes for Google Pub/Sub webhook endpoints.
"""

import json
import os
from datetime import datetime

import logfire
from fastapi import APIRouter, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool

from api.src.database.database import session_context
from api.src.google.common.service_account_auth import get_delegated_credentials
from api.src.google.gmail.checkpoint import advance_history_checkpoint, get_history_checkpoint
from api.src.google.gmail.config import GMAIL_WATCH_MAILBOX
from api.src.google.gmail.db_ops import save_email_message
from api.src.google.gmail.exceptions import has_unresolved_exception, record_unavailable
from api.src.google.gmail.service import (
    get_email_changes,
    get_email_content,
    get_gmail_service,
    process_single_message,
)
from api.src.google.pubsub.service import decode_pubsub_message, verify_pubsub_token

router = APIRouter(prefix="/pubsub", tags=["pubsub"])


# https://console.cloud.google.com/cloudpubsub/subscription/detail/gmail-notifications-sub?inv=1&invt=Abpamw&project=portfolio-450200
@router.post("/gmail/notifications")
async def handle_gmail_notifications(
    request: Request,
):
    """
    Receives Gmail push notifications from Google Pub/Sub.
    """
    try:
        # Record presence only: request headers can contain bearer tokens and cookies.
        logfire.info(
            "=== New Gmail Notification ===",
            authorization_present=bool(request.headers.get("authorization")),
        )

        # Verify the request is from Google Pub/Sub
        logfire.info("Verifying Pub/Sub token...")
        expected_audience = os.environ.get("PUBSUB_AUDIENCE_URL", "")
        await verify_pubsub_token(request.headers.get("authorization", ""), expected_audience)
        logfire.info("✓ Token verified")

        # Get the raw request body
        pubsub_body = await request.body()
        pubsub_body_str = pubsub_body.decode()
        logfire.info(f"Raw request body: {pubsub_body_str}")

        # Parse the message data
        pubsub_data = await request.json()
        logfire.info(f"Parsed JSON data: {json.dumps(pubsub_data, indent=2)}")

        if "message" not in pubsub_data:
            logfire.error("No 'message' field in request data")
            return Response(status_code=503, content="Missing message field")

        # Extract and decode the message data
        pubsub_message_data = pubsub_data["message"].get("data", "")
        if not pubsub_message_data:
            logfire.error("No 'data' field in message")
            return Response(status_code=503, content="Missing message data")

        try:
            # Decode and process the notification
            pubsub_decoded_json = decode_pubsub_message(pubsub_message_data)

            # Process the notification (uses per-message sessions to avoid pool exhaustion)
            processing_result = await process_gmail_notification(pubsub_decoded_json)

            # Handle response based on processing result
            if processing_result["status"] == "success":
                for email_msg in processing_result["messages"]:
                    logfire.info(
                        f"Processed email: {email_msg['subject']} from {email_msg['from_address']}"
                    )
                logfire.info(
                    f"✓ Successfully processed {len(processing_result['messages'])} messages"
                )
                return Response(status_code=204)
            elif processing_result["status"] == "no_messages":
                logfire.info(f"No messages to process: {processing_result['reason']}")
                return Response(status_code=204)
            elif processing_result["status"] == "partial_success_failure":
                logfire.info(f"Partial success: {processing_result['reason']}")
                return Response(status_code=500, content=processing_result["reason"])
            else:  # "retry_needed"
                logfire.warn(f"Retry needed: {processing_result['reason']}")
                return Response(
                    status_code=503,
                    content=processing_result["reason"],
                    headers={
                        "Retry-After": "5",
                        "X-Retry-Reason": processing_result.get(
                            "retry_reason", "gmail_processing_incomplete"
                        ),
                    },
                )

        except HTTPException:
            raise
        except Exception as e:
            logfire.exception("Failed to process notification")
            return Response(
                status_code=500,
                content=f"Failed to process notification (unhandled error1): {str(e)}",
            )

    except HTTPException:
        raise
    except Exception as e:
        logfire.exception("Unhandled error in Gmail notification handler")
        return Response(
            status_code=500,
            content=f"Failed to process Gmail notification (unhandled error2): {str(e)}",
        )


async def _process_gmail_history(
    pubsub_notification_data: dict, start_history_id: str, *, storage_only: bool = False
):
    """
    Process a Gmail notification and store messages in the database.

    Uses short-lived per-message sessions instead of a single long-lived session
    to avoid QueuePool exhaustion during bursts of notifications. Each email save
    checks out a connection only for the duration of the DB write, not during
    the slow Gmail API calls.

    Args:
        pubsub_notification_data: The decoded notification data from Pub/Sub
            Example: {
                "emailAddress": "user@example.com",
                "historyId": "12345"
            }

    Returns:
        Dictionary with:
        - status: "success", "no_messages", or "retry_needed"
        - messages: List of processed messages (if any)
        - reason: Explanation string for logger/debugging
    """
    # Enforce the mailbox boundary even for callers outside the HTTP handler.
    if (
        not isinstance(pubsub_notification_data, dict)
        or pubsub_notification_data.get("emailAddress") != GMAIL_WATCH_MAILBOX
    ):
        raise HTTPException(status_code=403, detail="Unauthorized Gmail notification mailbox")

    try:
        # Delegate only to the trusted mailbox, never a payload-selected subject.
        email_address = GMAIL_WATCH_MAILBOX
        history_id = pubsub_notification_data.get("historyId")

        if not email_address or not history_id:
            return {
                "status": "retry_needed",
                "messages": [],
                "reason": "Missing required fields in pubsub notification",
            }

        logfire.info(f"Processing notification for {email_address} with history ID: {history_id}")

        # Get Gmail service with delegated credentials
        credentials = get_delegated_credentials(
            user_email=email_address, scopes=["https://www.googleapis.com/auth/gmail.readonly"]
        )
        gmail_service = await run_in_threadpool(get_gmail_service, credentials)

        # Get message IDs from history with status
        email_changes_result = await get_email_changes(gmail_service, start_history_id)

        # If we got a non-success status, pass it through directly
        if email_changes_result["status"] != "success":
            logfire.info(
                f"Email changes status: {email_changes_result['status']} - {email_changes_result['reason']}"
            )
            return {
                "status": email_changes_result["status"],
                "messages": [],
                "reason": email_changes_result["reason"],
                "checkpoint": email_changes_result.get("checkpoint"),
                "has_more": email_changes_result.get("has_more", False),
                "retry_reason": email_changes_result.get(
                    "retry_reason", "gmail_processing_incomplete"
                ),
            }

        # email_message_ids: ALL messages touched (new + label changes, etc.) — process for DB.
        # added_message_ids: messages Gmail reports as genuinely new (often unreliable/empty).
        email_message_ids = email_changes_result["email_message_ids"]
        added_message_ids: set[str] = set(email_changes_result.get("added_message_ids", []))
        logfire.info(
            f"Found {len(email_message_ids)} messages to process ({len(added_message_ids)} new)"
        )

        processed_email_messages = []
        failed_email_ids = []
        unavailable_message_ids = []  # Messages with committed unresolved evidence

        # Process each message
        for email_message_id in email_message_ids:
            try:
                # Fetch and process message
                email_message = await get_email_content(gmail_service, email_message_id)

                if email_message is None:
                    await record_unavailable(
                        email_message_id,
                        f"history:{start_history_id}:{email_changes_result['checkpoint']}",
                        {
                            "source": "gmail.messages.get",
                            "http_status": 404,
                            "start_history_id": start_history_id,
                            "page_checkpoint": email_changes_result["checkpoint"],
                            "records": email_changes_result.get("message_history", {}).get(
                                email_message_id, []
                            ),
                        },
                    )
                    logfire.warning(
                        "Unavailable Gmail message retained as unresolved",
                        message_id=email_message_id,
                    )
                    unavailable_message_ids.append(email_message_id)
                    continue

                processed_email_message = await process_single_message(email_message)

                # Save to database using a short-lived session (will update if message exists).
                # This avoids holding a pooled connection during slow Gmail API calls.
                async with session_context() as save_session:
                    saved_msg, _was_inserted = await save_email_message(
                        save_session, processed_email_message, None if storage_only else history_id
                    )
                if saved_msg:
                    # Record Zillow emails in the durable queue BEFORE counting
                    # this message as processed. If the enqueue raises, the
                    # message lands in `failed_email_ids` only, the webhook
                    # answers non-2xx, and Pub/Sub delivers it again — the
                    # lead is not acknowledged until it is safely queued.
                    # The enqueue is idempotent (unique message_id) and
                    # ignores emails that are no longer fresh, so it is safe
                    # on every notification: new email, redelivery, or a
                    # label change.
                    from api.src.zillow_leads.queue import (
                        enqueue_zillow_email,
                        is_zillow_email,
                    )

                    from_addr = processed_email_message.get("from_address", "")
                    if is_zillow_email(from_addr or ""):
                        if storage_only or await has_unresolved_exception(email_message_id):
                            # Storage-only reconciliation must not silently skip a
                            # lead decision and then initialize the cursor. Live
                            # recovered exceptions likewise require review.
                            failed_email_ids.append(email_message_id)
                            continue
                        await enqueue_zillow_email(
                            message_id=processed_email_message.get("message_id", ""),
                            thread_id=processed_email_message.get("thread_id", ""),
                            subject=processed_email_message.get("subject"),
                            from_address=from_addr,
                            received_at=datetime.fromisoformat(processed_email_message["date"]),
                        )

                    processed_email_messages.append(processed_email_message)
                    logfire.info(
                        f"Successfully processed and saved message: "
                        f"{processed_email_message['subject']} (ID: {email_message_id})"
                    )
                else:
                    failed_email_ids.append(email_message_id)
                    logfire.error(f"Failed to save message {email_message_id}")

            except Exception as msg_error:
                failed_email_ids.append(email_message_id)
                logfire.exception(f"Failed to process message {email_message_id}: {str(msg_error)}")
                continue

        # Calculate the number of messages we actually attempted to process
        # (excluding skipped messages that were not found)
        attempted_message_count = len(email_message_ids) - len(unavailable_message_ids)

        # Only override the status if we had issues processing messages
        if attempted_message_count == 0:
            # All unavailable messages have durable unresolved evidence
            return {
                "status": "success",
                "checkpoint": email_changes_result["checkpoint"],
                "has_more": email_changes_result.get("has_more", False),
                "messages": [],
                "reason": f"All {len(unavailable_message_ids)} unavailable messages recorded as unresolved",
            }
        elif len(processed_email_messages) == attempted_message_count:
            # All attempted messages processed successfully
            return {
                "status": "success",
                "checkpoint": email_changes_result["checkpoint"],
                "has_more": email_changes_result.get("has_more", False),
                "messages": processed_email_messages,
                "reason": f"All {attempted_message_count} messages processed successfully. {len(unavailable_message_ids)} unavailable messages remain unresolved in the ledger.",
            }
        elif not processed_email_messages:
            # We found message IDs but couldn't process any - need retry
            return {
                "status": "retry_needed",
                "messages": [],
                "reason": f"Found {attempted_message_count} email messages but processed none. Failed IDs: {failed_email_ids}",
            }
        else:
            # Partial success - some messages processed, some failed
            return {
                "status": "partial_success_failure",
                "messages": processed_email_messages,
                "reason": f"Action required. Check logs for details. Processed {len(processed_email_messages)}/{attempted_message_count} email messages. Failed IDs: {failed_email_ids}. {len(unavailable_message_ids)} unavailable messages remain unresolved in the ledger.",
            }

    except Exception as e:
        logfire.exception(f"Failed to process Gmail notification: {str(e)}")
        return {
            "status": "retry_needed",
            "messages": [],
            "reason": f"Exception during processing: {str(e)}",
        }


async def process_gmail_notification(pubsub_notification_data: dict):
    """Drain from the last accounted-for checkpoint, not the notification's head.

    A notification is a wake-up, not a safe start cursor. No cursor is inferred
    from a notification or email row: initial reconciliation/seed requires review.
    """
    if (
        not isinstance(pubsub_notification_data, dict)
        or pubsub_notification_data.get("emailAddress") != GMAIL_WATCH_MAILBOX
    ):
        raise HTTPException(status_code=403, detail="Unauthorized Gmail notification mailbox")
    notified = str(pubsub_notification_data.get("historyId", ""))
    if not notified.isdecimal():
        raise HTTPException(status_code=400, detail="Invalid Gmail history ID")
    start = await get_history_checkpoint()
    if start is None:
        return {
            "status": "retry_needed",
            "messages": [],
            "retry_reason": "gmail_checkpoint_uninitialized",
            "reason": "Gmail checkpoint requires approved reconciliation and initialization",
        }
    # Even a redelivered/older notification drains outstanding pages. A page
    # may advance beyond this wake-up's ID while still having nextPageToken.
    result = await _process_gmail_history(pubsub_notification_data, start)
    if result["status"] in {"success", "no_messages"}:
        checkpoint = result["checkpoint"]
        await advance_history_checkpoint(start, checkpoint)
        if result.get("has_more"):
            return {
                "status": "retry_needed",
                "messages": [],
                "retry_reason": "gmail_history_remaining",
                "reason": "Processed one history page; more history remains",
            }
    return result
