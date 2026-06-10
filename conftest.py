"""Root pytest conftest — runs before any test module is imported.

pydantic-ai >=1.9x validates provider API keys when an Agent is constructed
(previously deferred to the first model request). Several agents are built at
import time (chat_emilio, sernia_agent), so test collection fails in
environments without real keys. Default test runs never hit real model APIs
(`live` tests are excluded via pytest.ini addopts), so dummy fallbacks are
safe. `setdefault` keeps real keys intact when present (needed for -m live).
"""
import os

os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")

# Several modules raise at import time when their service credentials are
# absent (clerk.py, open_phone/escalate.py, clickup/service.py,
# user/routes.py). The default suite never calls these services for real
# (live tests excluded; unit tests mock them), but pytest.ini collects every
# *.py under api/, so the imports must succeed. Dummies keep collection
# working in credential-less environments (CI, fresh clones); setdefault
# keeps real values intact when present.
os.environ.setdefault("CLERK_SECRET_KEY", "test-clerk-secret")
os.environ.setdefault("DEV_CLERK_WEBHOOK_SECRET", "test-clerk-webhook")
os.environ.setdefault("PROD_CLERK_WEBHOOK_SECRET", "test-clerk-webhook")
os.environ.setdefault("TWILIO_ACCOUNT_SID", "test-twilio-sid")
os.environ.setdefault("TWILIO_AUTH_TOKEN", "test-twilio-token")
os.environ.setdefault("CLICKUP_API_KEY", "test-clickup-key")
# api/index.py's startup check (required_env_vars) — needed because the test
# conftest imports `app`.
os.environ.setdefault("SESSION_SECRET_KEY", "test-session-secret")
os.environ.setdefault("GOOGLE_OAUTH_CLIENT_ID", "test-google-client-id")
os.environ.setdefault("GOOGLE_OAUTH_CLIENT_SECRET", "test-google-client-secret")
os.environ.setdefault("GOOGLE_OAUTH_REDIRECT_URI", "http://localhost:8000/api/oauth/callback")
os.environ.setdefault("OPEN_PHONE_WEBHOOK_SECRET", "test-openphone-webhook")
# utils/password.py unit test — any salt/hash works (it only asserts that
# wrong passwords fail).
os.environ.setdefault("ADMIN_PASSWORD_SALT", "test-salt")
os.environ.setdefault("ADMIN_PASSWORD_HASH", "not-a-real-hash")
