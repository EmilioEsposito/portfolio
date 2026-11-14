"""Defaults overridden by the dedicated zillow_lead_config setting."""

# Zillow email trigger defaults — overridable via DB-backed zillow_email_config setting.
# Debounce: first email starts a window; additional Zillow emails accumulate
# and the agent fires once at the end with the full batch.
DEFAULT_ZILLOW_DEBOUNCE_SECONDS = 300  # 5 minutes

# Approval stays on by default. The dedicated agent uses zillow_lead_config;
# the legacy zillow_email_config approval value is never inherited.
DEFAULT_ZILLOW_REQUIRE_APPROVAL = True
