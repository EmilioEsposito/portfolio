"""Evals for the lead agent: export real scenarios, run them, score them.

Offline only. Nothing the running service imports lives in this package.

- ``scenarios.py``: a scenario (a moment in a real thread) and the splits
- ``checks.py``: rules about a reply that code can check exactly
- ``labels.py``: a person's verdict on one output
- ``pages.py``: the review page and the dataset page, from ``templates/``
- ``judge.py``: an LLM judge that grades a decision against the instructions,
  with its prompt in ``prompts/judge.md``
- ``cli.py``: the commands below

    uv run python -m api.src.zillow_leads.evals export \
        --after 2026-04-01 --before 2026-08-01 --out $DATA/scenarios/scenarios.jsonl
    uv run python -m api.src.zillow_leads.evals split \
        --scenarios $DATA/scenarios/scenarios.jsonl --out $DATA/scenarios/splits.json
    uv run python -m api.src.zillow_leads.evals run \
        --scenarios $DATA/scenarios/scenarios.jsonl --listings $DATA/operating/operating.json \
        --splits $DATA/scenarios/splits.json --split dev \
        --name first-round --sample 30 --out $DATA/runs/first-round.jsonl
    uv run python -m api.src.zillow_leads.evals review \
        --scenarios $DATA/scenarios/scenarios.jsonl --listings $DATA/operating/operating.json \
        --run $DATA/runs/first-round.jsonl --out $DATA/runs/first-round.html

    uv run python -m api.src.zillow_leads.evals serve

A scenario is a moment in a real thread; listing data is what was true of
the business then. They are stored apart and joined into a case at run time,
so listings and schedules can change shape without touching the scenarios or
the labels people have put on them.

``$DATA`` is ``evals/data``, which Git ignores. Scenarios hold applicants'
emails: keep every path above inside it. Nothing here prints their content,
only counts.

``run --case-edits`` sets the tour state of some cases by hand, and ``--carry``
runs again only the cases of an earlier run that have a new edit.

``judge`` grades the outputs of one or more runs with the LLM judge and, given
the annotations store, reports how often it agrees with a person.

``collect`` adds a labels file downloaded from the review page to
``$DATA/annotations/annotations.jsonl``, the one store of every verdict.
``serve`` serves the pages in ``evals/data`` at http://localhost:8377, to this
machine only. ``run`` sends the experiment to Logfire when a token is configured. ``review``
writes one self-contained page for a person to mark each decision pass or
fail. ``browse`` writes a page for reading the train and dev splits, with
links into the review pages. Both pages hold applicant emails too.
"""
