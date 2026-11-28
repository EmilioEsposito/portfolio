"""Load the API environment for servers, tests, and standalone evals."""

import os

from dotenv import load_dotenv

# Only the deployed server entry point selects the hosted operations credential.
# Capture this before dotenv so copied Railway settings cannot select it locally.
HOSTED_API = os.getenv("PORTFOLIO_HOSTED_API") == "1" and bool(os.getenv("RAILWAY_REPLICA_ID"))

load_dotenv()

_sernia_anthropic_key = os.environ.get("SERNIA_ANTHROPIC_API_KEY")
if _sernia_anthropic_key:
    os.environ["ANTHROPIC_API_KEY"] = _sernia_anthropic_key


def operational_openrouter_key() -> str:
    name = "PORTFOLIO_OPENROUTER_API_KEY" if HOSTED_API else "LOCAL_PORTFOLIO_OPENROUTER_API_KEY"
    key = os.getenv(name, "").strip()
    if not key or not key.strip("* "):
        raise ValueError(f"{name} is required for operational inference")
    if not HOSTED_API and key in (
        os.getenv("PORTFOLIO_OPENROUTER_API_KEY", "").strip(),
        os.getenv("OPENROUTER_API_KEY", "").strip(),
        os.getenv("PUBLIC_PORTFOLIO_OPENROUTER_API_KEY", "").strip(),
    ):
        raise ValueError(f"{name} must be a separate development credential")
    return key
