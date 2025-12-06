"""
Gmail package initialization.
Exposes core functionality from the Gmail service.
"""

from apps.api.src.google.gmail.service import (
    send_email,
    get_gmail_service,
    setup_gmail_watch,
    stop_gmail_watch,
    get_email_changes,
    get_email_content,
    process_single_message,
    extract_email_body,
    create_message
)

__all__ = [
    'send_email',
    'get_gmail_service',
    'setup_gmail_watch',
    'stop_gmail_watch',
    'get_email_changes',
    'get_email_content',
    'process_single_message',
    'extract_email_body',
    'create_message'
]
