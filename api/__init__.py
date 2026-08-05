"""Package init — runs before anything under `api.*` is imported.

Bridges the app's explicitly-named provider keys to the names the SDKs and
pydantic-ai discover implicitly:

- SERNIA_ANTHROPIC_API_KEY -> ANTHROPIC_API_KEY. The app's key is stored under
  an explicit name because a variable literally named ANTHROPIC_API_KEY breaks
  Claude Code cloud sessions — it overrides the session's own authentication.
- PORTFOLIO_OPENROUTER_API_KEY -> OPENROUTER_API_KEY, which pydantic-ai's
  `OpenRouterProvider` reads. Same rationale for the prefix: keep the repo's
  own key namespaced so it can't collide with an agent tool's environment.

This lives in the package __init__ (not api/index.py) so EVERY entry point
gets it: the FastAPI app, pytest (pytest.ini collects inline tests from all
api/ modules, many of which construct Agents at import), seed_db.py, and ad
hoc scripts.
"""

import os

from dotenv import load_dotenv

# Local CLI keeps keys in .env; Railway/cloud inject real env vars. load_dotenv
# never overrides variables that are already set, so this is safe everywhere.
load_dotenv()

_sernia_anthropic_key = os.environ.get("SERNIA_ANTHROPIC_API_KEY")
if _sernia_anthropic_key:
    os.environ["ANTHROPIC_API_KEY"] = _sernia_anthropic_key

_portfolio_openrouter_key = os.environ.get("PORTFOLIO_OPENROUTER_API_KEY")
if _portfolio_openrouter_key:
    os.environ["OPENROUTER_API_KEY"] = _portfolio_openrouter_key
