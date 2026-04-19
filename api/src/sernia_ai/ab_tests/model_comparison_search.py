"""A/B experiment: tool-heavy (web search) comparison of model variants.

Each case mixes web-search and reasoning prompts, so tokens + cost reflect
real tool-calling behaviour. Input-token counts will diverge across providers
because tool-call results get injected back into context — see
`model_comparison_rote.py` for a pure-text comparison.

Run:
    python -m api.src.sernia_ai.ab_tests.model_comparison_search \\
        --experiment-name sernia-search-$(date +%Y-%m-%d)
"""
from __future__ import annotations

import asyncio

# Configure Logfire before importing the agent (which emits spans at import).
from api.src.utils.logfire_config import ensure_logfire_configured

ensure_logfire_configured(mode="prod", service_name="sernia_ai_ab_test")

from api.src.sernia_ai.ab_tests._cli import build_parser, resolve_variants
from api.src.sernia_ai.ab_tests._core import run_experiment


PROMPTS: list[str] = [
    "Search zillow.com for rentals near Pittsburgh PA 15213 under $2000 and summarize the top 3 in 2-3 bullet points each. Use web search.",
    "Use web search on rentometer.com to check typical rent for a 2-bedroom apartment in Squirrel Hill, Pittsburgh PA. Summarize what you find in under 50 words.",
    "A tenant just reported a leaky bathroom faucet. Walk through the steps you would take to triage this, without calling any tools.",
    "List three ways you could help the team prioritize maintenance tickets this week.",
]


def main() -> None:
    parser = build_parser(__doc__.split("\n\n")[0], default_experiment_prefix="sernia-search")
    args = parser.parse_args()

    asyncio.run(
        run_experiment(
            experiment_name=args.experiment_name,
            prompts=PROMPTS,
            variants=resolve_variants(args.variant),
            with_tools=args.with_tools,
            disable_builtin_tools=False,
            thinking=args.thinking,
            max_concurrency=args.max_concurrency,
        )
    )


if __name__ == "__main__":
    main()
