"""A/B experiment: pure-text baseline (no tools) comparison of model variants.

Clears every builtin tool and uses prompts that explicitly forbid tool use.
This isolates the raw model cost/token behaviour from tool-result noise — a
good sanity check that input-token counts roughly converge across providers
when no tools are called.

Run:
    python -m api.src.sernia_ai.ab_tests.model_comparison_rote \\
        --experiment-name sernia-rote-$(date +%Y-%m-%d)
"""
from __future__ import annotations

import asyncio

# Configure Logfire before importing the agent (which emits spans at import).
from api.src.utils.logfire_config import ensure_logfire_configured

ensure_logfire_configured(mode="prod", service_name="sernia_ai_ab_test")

from api.src.sernia_ai.ab_tests._cli import build_parser, resolve_variants
from api.src.sernia_ai.ab_tests._core import run_experiment


PROMPTS: list[str] = [
    "DO NOT USE ANY TOOLS. Answer in one sentence: what is rental real estate?",
    "DO NOT USE ANY TOOLS. List 5 common maintenance issues in apartment rentals. Plain text, one per line.",
    "DO NOT USE ANY TOOLS. Summarize in about 100 words what a property manager does.",
    "DO NOT USE ANY TOOLS. What's the typical process for resolving a noise complaint between tenants? Answer in 3 short paragraphs.",
]


def main() -> None:
    parser = build_parser(__doc__.split("\n\n")[0], default_experiment_prefix="sernia-rote")
    args = parser.parse_args()

    asyncio.run(
        run_experiment(
            experiment_name=args.experiment_name,
            prompts=PROMPTS,
            variants=resolve_variants(args.variant),
            with_tools=args.with_tools,
            disable_builtin_tools=True,
            thinking=args.thinking,
            max_concurrency=args.max_concurrency,
        )
    )


if __name__ == "__main__":
    main()
