# Legacy Zillow thread inference

This module is retained for reference and rollback. Its APScheduler registration
is disabled in `api/index.py`. Current Zillow email events and scheduled checks
use Sernia AI and its runtime model configuration, independently of these two
functions. Changing their models does not enable the legacy jobs or change the
active agent model.

`ai_assess_thread` returns the `ShouldReply` decision as a tuple.
`ai_collect_thread_info` returns `CollectedThreadInfo` and the cleaned thread.
Both call OpenRouter through the OpenAI SDK's JSON-schema `parse` endpoint.
They use `openai/gpt-6-luna` so the retained workflow can be exercised after
the January 14, 2027 retirement of GPT-4o mini on OpenRouter.

## Configuration and validation

Inference needs `PORTFOLIO_OPENROUTER_API_KEY`, obtained from
[OpenRouter's API key settings](https://openrouter.ai/settings/keys) and configured
in the existing service environment. Never commit credentials or use the capped
public demo key for this operational workflow. See [service configuration](../../../scripts/README.md).

Run the credential-free SDK contract tests:

```sh
uv run pytest api/src/tests/test_zillow_model_contract.py -q
```

Run the bounded live comparison from the repository root:

```sh
uv run python -m scripts.eval_zillow_models --output /private/path/zillow-eval.json
```

The runner resolves existing API service configuration, disables dotenv reloads,
uses inert database URLs, and disables Logfire export before importing the
service. It calls only the two inference functions and blocks their downstream
database, SMS, contact, and calendar interfaces. It uses 12 synthetic threads,
two schemas, two models, and two repeats (96 requests), sequentially, with a
30-second timeout and no retries. Model order alternates across repeats.
`--limit 2 --repeats 1` provides an eight-request smoke test.

Labels are synthetic policy expectations, not production accuracy measurements
or human-adjudicated results. Classification compares both booleans; extraction
compares all seven fields, ignoring phone punctuation. Reason wording is not
scored. Collection is tested on every thread to stress null handling, although
the legacy workflow only invokes it when assessment detects an appointment.

The private JSON report records dataset/source hashes, source commit, settings,
per-call outputs, mismatches, provider-reported usage/cost, and wall time. A failed
call stops the run and preserves completed results. Use a fresh output file for
each run. The GPT-4o mini baseline will stop working after retirement; remove it
from future comparisons rather than interpreting an API failure as a verdict.

For schema failures, inspect the saved error type and run the contract tests.
For availability/authentication failures, verify the configured key and current
[OpenRouter model availability](https://openrouter.ai/models). Do not run the
legacy `test_check_email_threads` live test for a model comparison: it can send
SMS and update external contacts and calendar events.

## October 20, 2026 comparison

All 96 requests parsed successfully (12 threads × two schemas × two models ×
two repeats). Prompts, preprocessing, schemas, and generation settings were
unchanged; the runner overrode only the model ID.

| Measure | GPT-4o mini | GPT-6 Luna |
| --- | ---: | ---: |
| Assessment matches synthetic labels | 21/24 | 24/24 |
| Complete extraction matches synthetic labels | 9/24 | 24/24 |
| Provider-reported total cost, 48 calls | $0.0079428 | $0.0064235 |
| Median request wall time | 1.21 s | 2.21 s |

Cost was present on all 96 responses. Luna cost 19.1% less in this cohort but
was about one second slower at the median. This is a small, sequential local
comparison, with no control over provider routing or caching. Repeated cases
are not independent samples of production traffic.

Assessment agreement was 21/24: GPT-4o mini unnecessarily followed up on a
lead who had not answered Sernia's questions (both repeats), and on unaccepted
viewing options (one repeat). Appointment-detected flags agreed on all 24.
Extraction agreement was 9/24. GPT-4o mini invented appointment dates when
none was confirmed, selected unaccepted viewing options, inferred absent names,
and once returned an email address as the lead phone number. Luna matched the
synthetic expectations on each disagreement. The null-handling stress cases
are broader than extraction's normal conditional invocation in the legacy job.

The seven offline tests passed using the real OpenAI SDK parser with mocked
HTTP, covering both response schemas, nullable extraction, and invalid payload
rejection. Repository-wide Ruff lint and format checks also passed.

For reference, published standard token rates at the time of the run were
[$0.15 input / $0.60 output per million for GPT-4o mini](https://openrouter.ai/openai/gpt-4o-mini)
and [$0.10 / $0.50 for GPT-6 Luna](https://openrouter.ai/openai/gpt-6-luna).
Reasoning and caching can change per-request totals; the table uses billed
usage rather than multiplying headline prices.

The migration is limited to the two retained inference calls. It prevents a
known retirement failure if they are manually exercised or restored; current
Sernia AI workflows and the disabled legacy scheduler registration are unchanged.
