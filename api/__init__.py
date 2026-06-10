"""Package init — runs before anything under `api.*` is imported.

Bridges ANTHROPIC_API_KEY_PORTFOLIO -> ANTHROPIC_API_KEY for the Anthropic
SDK and pydantic-ai (both discover the key implicitly via ANTHROPIC_API_KEY).
The app's key is stored under an explicit name because a variable literally
named ANTHROPIC_API_KEY breaks Claude Code cloud sessions — it overrides the
session's own authentication.

This lives in the package __init__ (not api/index.py) so EVERY entry point
gets it: the FastAPI app, pytest (pytest.ini collects inline tests from all
api/ modules, many of which construct Agents at import), seed_db.py, and ad
hoc scripts.

SERNIA_ANTHROPIC_API_KEY is the deprecated former name — honored as a
fallback until all environments (Railway, local .env files) are renamed.
"""
import os

from dotenv import load_dotenv

# Local CLI keeps keys in .env; Railway/cloud inject real env vars. load_dotenv
# never overrides variables that are already set, so this is safe everywhere.
load_dotenv()

_portfolio_anthropic_key = os.environ.get("ANTHROPIC_API_KEY_PORTFOLIO") or os.environ.get(
    "SERNIA_ANTHROPIC_API_KEY"  # deprecated name
)
if _portfolio_anthropic_key:
    os.environ["ANTHROPIC_API_KEY"] = _portfolio_anthropic_key
