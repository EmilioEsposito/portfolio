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
