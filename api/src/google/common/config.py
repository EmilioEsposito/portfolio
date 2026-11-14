"""Sernia Workspace accounts used by operational integrations."""

# Google API delegation requires impersonating a real Google Workspace user.
GOOGLE_DELEGATION_EMAIL = "emilio@serniacapital.com"

# Internal email domain — emails to this domain bypass HITL approval.
INTERNAL_EMAIL_DOMAIN = "serniacapital.com"

# Shared external mailbox for outbound email and calendar invites.
# Using the shared mailbox ensures attendees receive email invites
# (self-organized events via delegation don't trigger notifications).
SHARED_EXTERNAL_EMAIL = "all@serniacapital.com"
