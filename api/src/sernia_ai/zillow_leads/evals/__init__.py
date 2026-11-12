"""Evals for the lead agent: export real scenarios, run them, score them.

Offline only. Nothing the running service imports lives in this package.

- ``scenarios.py``: a scenario (a moment in a real thread) and the splits
- ``checks.py``: rules about a reply that code can check exactly
- ``labels.py``: a person's verdict on one output
- ``pages.py``: the review page and the dataset page, from ``templates/``
- ``cli.py``: the commands below

    uv run python -m api.src.sernia_ai.zillow_leads.evals export \
        --after 2026-04-01 --before 2026-08-01 --out <private>/scenarios.jsonl
    uv run python -m api.src.sernia_ai.zillow_leads.evals split \
        --scenarios <private>/scenarios.jsonl --out <private>/splits.json
    uv run python -m api.src.sernia_ai.zillow_leads.evals run \
        --scenarios <private>/scenarios.jsonl --operating <private>/operating.json \
        --splits <private>/splits.json --split dev \
        --name first-round --sample 30 --out <private>/runs/first-round.jsonl
    uv run python -m api.src.sernia_ai.zillow_leads.evals review \
        --scenarios <private>/scenarios.jsonl --operating <private>/operating.json \
        --run <private>/runs/first-round.jsonl --out <private>/runs/first-round.html

A scenario is a moment in a real thread; operating data is what was true of
the business then. They are stored apart and joined into a case at run time,
so listings and schedules can change shape without touching the scenarios or
the labels people have put on them.

Scenarios hold applicants' emails. Keep every path above outside the
repository; nothing here prints their content, only counts.

``run`` sends the experiment to Logfire when a token is configured. ``review``
writes one self-contained page for a person to mark each decision pass or
fail. ``browse`` writes a page for reading the train and dev splits, with
links into the review pages. Both pages hold applicant emails too.
"""
