# Gmail Notification System Documentation

This document explains how Gmail notifications are handled in the system using Google Pub/Sub.

## Overview

The system uses Google Pub/Sub to receive Gmail notifications when new emails arrive. The general flow is:

1. Gmail API watch function monitors a mailbox
2. Gmail notifies our Pub/Sub topic when changes occur
3. Pub/Sub delivers a notification to our webhook endpoint
4. Our system processes the notification and fetches the new emails
5. Emails are saved to our database

## Flow Diagram

```mermaid
flowchart TD
    %% External systems
    email[Email arrives in Gmail]
    
    %% Define the file groupings
    subgraph gmail_service["api_src/google/gmail/service.py"]
        get_email_changes["2.1-get_email_changes(service, history_id)"]
        status_decision{Check for messages}
        get_email_content["2.2-get_email_content(service, email_message_id)"]
        process_single_message["2.3.0-process_single_message(email_message)"]
        extract_email_body["2.3.1-extract_email_body(email_message)"]
    end
    
    subgraph pubsub_routes["api_src/google/pubsub/routes.py"]
        handle_gmail_notifications["1-handle_gmail_notifications(request, session)"]
        process_gmail_notification["2.0-process_gmail_notification(pubsub_notification_data, session)"]
        return_status["3-Return HTTP status code"]
    end
    
    subgraph gmail_db_ops["api_src/google/gmail/db_ops.py"]
        save_email_message["2.4.0-save_email_message(session, email_message, history_id)"]
        get_email_by_message_id["2.4.1-get_email_by_message_id(session, email_message_id)"]
    end
    
    %% Define the flow
    email --> pubsub[Google Pub/Sub]
    pubsub --> handle_gmail_notifications
    
    %% Simplified flow
    handle_gmail_notifications -- "calls with pubsub_decoded_json" --> process_gmail_notification
    
    %% Process notification flow
    process_gmail_notification -- "calls with history_id" --> get_email_changes
    
    %% Status decision now happens in get_email_changes
    get_email_changes -- "makes" --> status_decision
    status_decision -- "success: email_message_ids found" --> return_success["Return success result"]
    status_decision -- "no_messages: no changes" --> return_no_messages["Return no_messages result"]
    status_decision -- "retry_needed: needs retry" --> return_retry["Return retry_needed result"]
    
    %% Different paths based on status
    return_success -- "returns to" --> process_gmail_notification
    return_no_messages -- "returns to" --> process_gmail_notification
    return_retry -- "returns to" --> process_gmail_notification
    
    %% For successful results with messages
    process_gmail_notification -- "for each ID, calls" --> get_email_content
    get_email_content -- "returns email_message to" --> process_gmail_notification
    
    process_gmail_notification -- "calls with email_message" --> process_single_message
    process_single_message -- "calls" --> extract_email_body
    extract_email_body -- "returns to" --> process_single_message
    process_single_message -- "returns processed email_message to" --> process_gmail_notification
    
    process_gmail_notification -- "calls with email_message" --> save_email_message
    save_email_message -- "may call" --> get_email_by_message_id
    get_email_by_message_id -- "returns to" --> save_email_message
    save_email_message -- "returns to" --> process_gmail_notification
    
    %% Result determination
    process_gmail_notification -- "returns processing_result to" --> handle_gmail_notifications
    
    handle_gmail_notifications --> return_status
    return_status -- "204: Success/No msgs" --> return_204["Return 204 (Success)"]
    return_status -- "503: Retry needed" --> return_503["Return 503 (Retry)"]
    
    return_503 -- "triggers retry of" --> pubsub
    
    %% Styling
    classDef external fill:#ddd,stroke:#333,stroke-width:1px
    classDef routes fill:#bfb,stroke:#333,stroke-width:2px
    classDef gmail fill:#f9f,stroke:#333,stroke-width:2px
    classDef db fill:#fdb,stroke:#333,stroke-width:2px
    
    class email,pubsub external
    class handle_gmail_notifications,process_gmail_notification,return_status,return_204,return_503 routes
    class get_email_changes,status_decision,return_success,return_no_messages,return_retry,get_email_content,process_single_message,extract_email_body gmail
    class save_email_message,get_email_by_message_id db
```

## Key Functions and Flow

### 1. `handle_gmail_notifications`

This is the entry point - a FastAPI route that receives notifications from Google Pub/Sub.

**Responsibilities:**
- Validate the request is from Google Pub/Sub
- Extract and decode the message data
- Pass the decoded data to the processor
- Return appropriate status code based on processing results

**Status Codes:**
- `204`: Successfully processed or confirmed no messages to process
- `503`: Temporary failure; Pub/Sub should retry

### 2. `process_gmail_notification`

This function processes the notification and fetches any new emails.

**Responsibilities:**
- Get Gmail service with appropriate credentials
- Fetch message IDs using history ID from the notification
- Process each message and save to database
- Return a structured result with processing status

**Return Value Structure:**
```python
{
    "status": "success" | "no_messages" | "retry_needed",
    "messages": [list_of_processed_messages],
    "reason": "Explanation of what happened"
}
```

### 3. `get_email_changes`

This function fetches the list of message IDs that have changed since a specific history ID and makes the primary status decision.

**Responsibilities:**
- Query Gmail API for changes since the given history ID
- Use exponential backoff to handle race conditions 
- Make the primary status determination:
  - `success`: Found history and new message IDs
  - `no_messages`: Found history but no new messages
  - `retry_needed`: Could not retrieve history (needs retry)
- Return both status and message IDs in a structured format

**Return Value Structure:**
```python
{
    "status": "success" | "no_messages" | "retry_needed",
    "email_message_ids": [list_of_message_ids],
    "reason": "Explanation of what happened"
}
```

### 4. `get_email_content` and `process_single_message`

These functions fetch and process individual email messages.

**Responsibilities:**
- Fetch full message content from Gmail API
- Extract relevant information (subject, body, etc.)
- Format into a structure ready for database storage

## Retry Logic and Status Codes

The system is designed to handle temporary failures gracefully:

### When 204 (Success) is Returned
- When all messages are successfully processed
- When we've confirmed there are no messages to process for this notification
- This tells Pub/Sub the message was handled successfully

### When 503 (Service Unavailable) is Returned
- When Gmail history ID isn't available yet (race condition)
- When we found message IDs but couldn't process them
- When unexpected errors occur during processing
- This tells Pub/Sub to retry the notification later

## Pub/Sub Configuration

For reliable processing, the Pub/Sub subscription should have:
- Acknowledgement deadline: At least 120 seconds
- Retry policy: Exponential backoff (currently 10s min, 600s max)

## Common Issues and Debugging

- **Same notification appears multiple times**: This is normal if the Gmail API hasn't made the messages available yet. The 503 status code triggers Pub/Sub to retry.
- **"No new messages found"**: The Gmail API sometimes sends notifications before changes are available. The system will retry until it can access the messages.
- **Messages not appearing in database**: Check the logs for specific message processing errors.

### Status Flow

Status determinations flow through the system as follows:

1. **Primary Status Decision** - Made in `get_email_changes`:
   - Determines if history is available and if there are messages to process
   - This status is passed directly to the caller for "no_messages" and "retry_needed" cases

2. **Status Override** - Only happens in `process_gmail_notification` when needed:
   - Maintains "success" status if all messages are processed successfully
   - Overrides to "retry_needed" if message processing fails
   - Statuses from `get_email_changes` are never overridden for "no_messages" or "retry_needed"

3. **HTTP Response Status** - Determined in `handle_gmail_notifications`:
   - 204: For "success" and "no_messages" results (acknowledged)
   - 503: For "retry_needed" results (temporary failure, retry) 