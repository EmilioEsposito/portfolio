# OpenPhone escalation assessment

## Private sender/topic suppression

Set `ESCALATION_SUPPRESSION_RULES` on Railway's **fastapi** service to a JSON array.
Keep actual identities and matching terms in private service configuration, never
in source, PR descriptions, or fixtures. Empty/unset or `[]` disables these rules.
Synthetic schema example:

```json
[{"from_numbers":["+15555550101","+15555550102"],"match_terms":["routine topic"],"bypass_terms":["separate emergency"]}]
```

Each rule requires a sender list, topic phrases, and bypass phrases. Matching is
case-insensitive, ignores punctuation/extra whitespace, and uses whole words or
phrases (list inflections explicitly). Sender matching accepts E.164 and US display
formats. Only the current message is matched; history never mutes a new topic.
Any bypass phrase across the sender's rules resumes normal AI assessment, even
when a topic matches. Choose bypass phrases for independent urgent incidents.
This is a literal routing rule, not a semantic classifier: unlisted paraphrases
follow normal assessment, and a matching topic suppresses a mixed message unless
it also contains a configured bypass phrase. Review the phrases accordingly.

The shared assessment entry point applies the rule before history/model calls,
covering direct handling and durable webhook/recovery processing. Suppressed
outbox requests become `skipped` without creating deliveries. Messages remain in
Quo and the event database; this does not mute the sender or change other workflows.
Already-created deliveries and explicit manual escalation tools are unaffected.
Logs contain only a generic suppression marker, not private configuration values.
Malformed configuration raises a fixed diagnostic; durable assessment retries and
eventually requires manual attention rather than silently ignoring the configuration.
To revert, set the variable to `[]` and redeploy. Changes require a deployment to
reach the running process. Test with synthetic events and mocked delivery only.

**Luna is the code default.** Production can explicitly enable `either` to run
Luna and Jev on every assessment with OR alerting. Jev also remains available for experiments. Both use the same awareness policy, best-effort
Quo history, and precomputed elapsed times. Images are still eval-only.

## Easy-to-follow decision flow

1. `escalate.ai_assess_for_escalation` retrieves history once and builds one input.
2. `escalation_assessment.assess_state` selects the configured model(s).
3. Each classifier returns a typed verdict; failures have `should_escalate=None`.
4. `combine_assessments` returns one final decision, retaining every model result.
5. The durable outbox records the decision and queues one delivery per recipient.
   Classifier functions cannot send SMS or place calls.

`escalation_policy.py` holds the shared prompt. `escalation_context.py` handles
Quo retrieval and timing. `typesafe_openrouter.py` retains the TypeSafe SDK adapter.

### Explicit model modes

Set `ESCALATION_MODEL_MODE` in the service configuration (unset means `luna`):

| Mode | Runs | Alert decision |
|---|---|---|
| `luna` (default) | Luna, low reasoning | Luna's successful verdict |
| `jev` | Jev | Jev's successful verdict |
| `either` (opt-in) | Both concurrently, same input | Any successful positive verdict |

Unknown modes raise a configuration error; they never silently enable both.
OR mode can improve recall but also inherits either model's false positives;
it increases inference cost and waits for both bounded assessments. Each model
has a 30-second per-attempt limit and one retry by default, in parallel for
`either`, plus a shared five-second best-effort history lookup. A positive from
one model still wins if the other fails. With no positive and one/both failures,
the assessment remains retryable without placing a call; failure details remain
in telemetry.
The outbox deduplicates provider message IDs and agent tool-call IDs. Distinct
messages still receive independent assessments; it does not guess that two
messages describe the same incident.

Logfire records the exact shared input/policy, selected mode, per-model verdicts
and errors, and final decision. Luna retains PydanticAI instrumentation; Jev logs
provider-reported usage, cost and probabilities. Jev's reason is a probability
summary, not generated explanatory prose. No confidence threshold is introduced.

Unset `ESCALATION_MODEL_MODE` selects Luna. Set it to `either` to enable paired
production inference and OR alerting; set it back to `luna` to roll back the mode.
A service redeploy is required for environment changes to affect running workers.

## OpenRouter dependency

- Uses the existing server-side `PORTFOLIO_OPENROUTER_API_KEY`; create/manage
  operational keys at https://openrouter.ai/settings/keys. Never use the public
  demo key or a TypeSafe key here.
- Model: `typesafe/jev-1.13`.
- Endpoint: `POST https://openrouter.ai/api/alpha/decisions`.
- The account's allowed-provider list must include **TypeSafe**. A 404 saying
  `No allowed providers are available` is an account routing restriction, not
  a missing model. Review https://openrouter.ai/settings/privacy; do not relax
  the account's other privacy controls to fix this.
- `typesafe_openrouter.py` supplies an `httpx2` request hook that rewrites the
  SDK's `/v1/systemone` path to `/api/alpha/decisions`. The SDK still serializes
  typed `Choice` questions and validates responses. The adapter intentionally
  supports only `system_one`, not model-listing methods.
- OpenRouter's actual `usage.cost` and token counts are attached to the Logfire
  assessment span, along with the verdict/probabilities. There is no estimated
  price fallback when cost is absent.
- Each attempt has a 30-second wall-clock limit. The caller retries once by
  default; SDK retries are disabled. Failed assessments retain the existing
  no-escalation behavior. Excluded senders and Twilio dispatch are unchanged.

References: [TypeSafe Python SDK](https://docs.typesafe.ai/sdk/python),
[Choice](https://docs.typesafe.ai/primitives/choice),
[OpenRouter Decisions API](https://openrouter.ai/docs/api/api-reference/alphadecisions/submit-a-decisions-questions-and-answers-request).
The endpoint is currently an alpha API; its contract is covered by mocked
HTTP tests and an opt-in live check.

## Best-effort conversation context

The candidate reads history directly from the Quo API using `QUO_API_KEY_PORTFOLIO`
(the existing Quo integration credential). It never falls back to the events
DB. It resolves the inbox number, queries the exact participant set, and checks
returned conversation IDs. See [Quo list messages](https://www.quo.com/docs/api-reference/messages/list-messages).

History is limited to the latest 20 text messages in the preceding 72 hours,
with one API page of at most 100 records and a 16,000-character payload cap.
The entire lookup has a 5-second budget in addition to the inference timeout.
Failure or missing thread identity preserves assessment of the incoming text;
`history_status` records missing, unavailable, or partial context. Current and
future messages, later-edited messages, and media-only records are excluded.
No images are downloaded or interpreted. Incoming timestamps and history are
represented in America/New_York, including daylight saving time.

The previous classifier had no conversation lookback. The candidate logs the
exact `escalation.input` and `escalation.question` snapshots alongside its output,
so future replays need not reconstruct context from mutable API data.

## Verification

```sh
uv run python -m pytest api/src/tests/test_escalation_assessment.py api/src/tests/test_escalation_context.py api/src/tests/test_jev_escalation.py api/src/tests/test_escalation_tools.py
uv run python -m pytest api/src/tests/test_jev_escalation.py -m live -x
```

The live suite sends 14 synthetic tenant-message examples for assessment only.
It never invokes Twilio, sends SMS, or reads production tenant records. Labels
are synthetic expectations derived from the existing policy, not a production
accuracy benchmark. It prints each decision and end-to-end latency. Do not
promote this replacement without a successful live evaluation.

### Live evaluation: 2026-09-18

After TypeSafe was added to the OpenRouter account provider allowlist, all
14 synthetic policy cases passed through the SDK adapter: 6 escalate and
8 do-not-escalate. No request failures occurred and retries were disabled.
Median end-to-end assessment latency was 0.311s
(range 0.253–0.413s). This includes client setup, network,
SDK decoding, and local instrumentation.

Coverage includes active water damage, fire, break-in, active drug dealing and
harassment, routine maintenance, resolved damage, lockout/power-outage policy
exclusions, claimed urgency, and an instruction-injection example. These are
small synthetic smoke evals, not evidence of production accuracy or superiority
over the previous model. No Twilio calls or SMS were sent.

### Ceiling incident reconstruction: 2026-09-18

Two text events from August 9 were evaluated with and without preceding Quo
history. Their UTC event times (05:02 and 05:16) are **01:02 and 01:16 EDT**.
Luna escalated both the original “feeling fell” typo and the corrected “ceiling
fell” text, with and without history. Jev escalated only the correction, even
when given the earlier ceiling/water-damage exchange (9 and 10 prior text
messages respectively). These are new model outputs, not recovered historical
verdicts, and are single runs per condition. The original typo remains an
important disagreement; the all-negative production replay and smoke cases
are insufficient grounds to promote Jev. After adding context, 27 mocked/local
tests and all 14 live policy smoke cases passed. No dispatch was performed.

Private inputs and results remain outside Git under the local Codex replay
artifact directory, since they contain tenant communications.

### History noise and image eval: 2026-09-18

The candidate policy now asks whether a **new** urgent notification is warranted.
Resolved or unrelated history, acknowledgements, access coordination, answers to
questions, and unchanged status while management is actively handling the same
incident should remain quiet. Material deterioration, new emergencies, recurrence,
and failed response to ongoing danger still escalate. A generic automated reply
or unrelated outbound text does not prove active handling. This is contextual
classification, not durable incident-level notification deduplication.

Run the inference-only comparison (no dispatch, production queries or telemetry):

```sh
uv run python -m scripts.eval_escalation_context --output /private/path/fresh-run.json
```

`api/src/tests/eval_data/escalation_history_cases.json` contains 16 synthetic cases
(8 positive, 8 negative) with explicit label provenance and rationales. Two runs
per case yielded 32/32 for both Luna and Jev, zero errors, zero false alarms in
16 negative runs per model, and zero missed alerts in 16 positive runs per model.
This designed cohort does not estimate a production false-alarm rate or erase
the earlier ceiling-typo disagreement. The original 14 live cases still pass.

Separately, Luna successfully accepted the actual Quo ceiling photo using
PydanticAI `BinaryContent`: it escalated the image-only event and later correction,
and stayed quiet for a synthetic repaired-incident follow-up with the same image
explicitly identified as old. These are three single-run assessments. The photo
arrived after the typo, so it was never added to that earlier event retroactively.
Images remain **eval-only**; operational assessments currently use text plus history.
Any production image integration must retain attachment timestamps, handle
image-only events, bound downloads, and tolerate unavailable media.

### Receiver-awareness rule

The escalation goal is to make the receiving management team aware of urgent
situations, not to remind them that acknowledged damage remains unrepaired.
An explicit outbound acknowledgement of the same emergency within the preceding
30 minutes (inclusive), measured against the incoming event timestamp, suppresses
a repeat alert for an unchanged ongoing issue. Acknowledgement alone is enough;
no dispatch or repair promise is required. Generic automated receipts and unrelated
outbound texts do not establish awareness of that emergency.

Material new danger, deterioration, a distinct emergency, or a failed response
can still need renewed awareness. Passing 30 minutes does not itself cause an
alert: routine replies remain quiet. The synthetic history cohort now includes
29-, 30-, and 31-minute cases, acknowledgement without a dispatch plan, and a
specific acknowledgement embedded in a question.

Live verification of the awareness policy: Luna passed 22/22 synthetic cases;
Jev passed 21/22. Both suppressed the unchanged ongoing incident at 29 and exactly
30 minutes. Jev missed the expected alert at 31 minutes when the tenant explicitly
reported continued water entering walls, no action, and asked for immediate help.
This is another observed Jev disagreement, not a reason to tune on the same case
until it passes. Local/mock regression tests: 27 passed. No deployment.

### Precomputed timing

The candidate now adds event-relative seconds/minutes and an inclusive
`within_previous_30_minutes` flag to each history message, plus summary ages for
the last supplied message and last supplied outbound message. UTC subtraction
handles DST; missing ages are null. These facts refer only to supplied history.
They do not label an outbound message as an acknowledgement: semantic relevance
remains the model's job.

With the prompt unchanged, a paired Jev eval across 22 synthetic cases repeated
three times yielded 63/66 with raw timestamps and 66/66 with computed timing.
The previously failing 31-minute case changed from 0/3 to 3/3; the 29- and
30-minute cases remained quiet. This supports supplying temporal facts but does
not isolate arithmetic as the sole cause or establish production accuracy.

```sh
uv run python -m scripts.eval_escalation_context --model Jev --timing both --repeats 3 --output /private/path/fresh.json
```

The runner defaults to computed timing, matching the candidate assessment path.
Use `--timing raw` for the historical input shape. Timing-helper boundary/DST
coverage and existing tests passed (35 local/mock tests). Nothing deployed.

### Ceiling typo added to the current regression cohort

The fixture now includes synthetic ceiling-typo and correction cases with nine/ten
preceding text messages and computed timing. With the current prompt unchanged,
Luna escalated both in 3/3 runs. Jev missed the typo in 3/3 and escalated the
correction in 3/3. A separate private replay using actual Quo text history produced
the same result. No photos or future correction text were given to the typo case.
Thus improved timing does not remove the typo disagreement. The earlier perfect
22-case result should not be read as parity on the expanded 24-case cohort.
Use `--dataset /path/to/cases.json` to run a bounded selected cohort.

### Luna default and optional OR orchestration verification

The restored Luna operational function passed all 24 synthetic history cases,
including the typo, with recorded history injected at the fetch boundary and
real inference. This exercises input assembly and timing as well as the model.
50 local tests cover default selection, the OR truth table, separate failure
states, timeout handling, concurrent calls, and exactly one dispatch when both
models return positive. The 14 Jev live smoke cases still pass. No live tests
called notification dispatch. Deployment and OR activation remain separate
from these local code changes.

## Paired production telemetry

One completed `Escalation assessment` span represents one input, not one provider
attempt. It stores a unique `assessment_id`, source event ID, exact input/policy
and their SHA-256 hashes, mode, final decision, per-model verdicts, error count,
and `escalation.comparable` / `escalation.agreement`. Agreement is null when either
model fails or only one model ran; failed calls are not negative votes.

Each model has its own direct child `Escalation model assessment` span, with the
same `assessment_id`, normalized output, provider model ID, error status, attempt
count, and elapsed latency including retries. Nested `Escalation model attempt`
spans distinguish individual tries. `escalation.usage` contains reported tokens
and cost for the successful attempt, or null when unavailable.
The escalation-only Luna adapter preserves the gateway's explicit prompt/completion
token counts: the pinned SDK's pricing-based extractor otherwise reports zeros
for this model. Counts are never inferred from billed cost. It is not total
billed cost across failed attempts. Luna native LLM spans and the Jev details log
retain canonical `operation.cost`; the normalized summaries use a different
namespace to prevent double-counting that cost. Missing cost is not zero.

`escalation.sample_kind` distinguishes normal `production` inputs from explicitly
marked `verification` and `eval` inputs. Always filter the deployment environment
as well: local tests are not production samples. Count only `kind = 'span'`,
not pending spans. The root result is the authoritative completed pair.

Example agreement query for Logfire Explore (select a bounded time range):

```sql
SELECT
  attributes->>'escalation.luna_verdict' AS luna_verdict,
  attributes->>'escalation.jev_verdict' AS jev_verdict,
  attributes->>'escalation.comparable' AS comparable,
  attributes->>'escalation.error_count' AS error_count,
  count(*) AS samples
FROM records
WHERE service_name = 'fastapi'
  AND deployment_environment = 'production'
  AND kind = 'span'
  AND span_name = 'Escalation assessment'
  AND attributes->>'mode' = 'either'
  AND attributes->>'escalation.sample_kind' = 'production'
GROUP BY 1, 2, 3, 4
```

Use the same trace and `assessment_id` to inspect the two model child spans.
Keep retries, failed pairs and verification runs out of the agreement denominator.
Human review of sampled disagreements and agreements is still needed to measure
precision/recall; agreement alone does not establish correctness. OR also changes
alerting behavior: a Jev-only positive now triggers a notification, which can
increase false positives as well as recall. Dispatch still runs once per event.

### Jev agent review

Jev emits an `invoke_agent` span named `escalation_assessor_jev`, alongside Luna's
existing `escalation_assessor`. Its input, policy, decision probabilities, and
final output are available in Logfire Agents and Quick annotate. A nested
`Jev decision` model span owns tokens and cost; cost is not duplicated on the
agent or comparison spans. Failed attempts record errors without inventing an output.

The existing `scripts/eval_escalation_context.py` is a local JSON comparison
runner and explicitly disables Logfire export. It does **not** create hosted
Datasets & Experiments entries or synchronize human annotations. Production
comparison traces and human run annotations are separate from offline eval scores.
Reviewed labels must be explicitly promoted into a versioned regression dataset.


## Durable emergency delivery

`escalation_delivery.py` owns the database outbox. The webhook inserts its event
with `ON CONFLICT DO NOTHING` and queues the assessment in the **same transaction**.
A process exit after the HTTP response no longer loses the work. The scheduler
recovers pending assessments and deliveries every 15 seconds under the existing
background-automation opt-in policy. Immediate request dispatch still works when
background automation is off; unattended retries require the scheduler.

Migration `0032_escalation_outbox` creates three empty tables and does not backfill
historical webhooks. Source keys use the Quo message ID (event ID fallback) or
conversation ID plus agent tool-call ID. The request/recipient hashes are stable
across retries. Default contact slugs remain Emilio and Peppino; each lookup is a
durable work item, its number freezes once resolved, and duplicate resolved phone
numbers within a request are sent only once. No tenant/recipient list changes are
part of this feature.

Each provider POST has a committed `escalation_attempts` record first. Database
row locks and 120-second leases fence concurrent workers. An interrupted `sending`
attempt becomes `unknown`, never a fresh send. Per-recipient outcomes are:

| State | Meaning / recovery |
|---|---|
| `pending`, `retry` | Not accepted; safe work is due at `next_attempt_at`. |
| `sending` | POST may be in flight. Never start another POST for this attempt. |
| `blocked` | A different Studio execution is active; read it until it ends. |
| `unknown` | Acceptance is uncertain; use GET-only reconciliation. |
| `accepted` | Twilio returned a valid execution SID, or context verified the exact correlation token, flow, sender, and recipient. **Human receipt is unconfirmed.** |
| `failed` | Permanent provider rejection; operator attention required. |
| `manual` | Deadline/retry budget exhausted; inspect before any manual action. |
| `duplicate` | Another recipient key in this request already owns this phone. |

Known active executions are checked before a later request. A racing Studio 409
(`20409`) stores the conflicting SID instead of throwing a failed-send exception.
Only a matching `flow.data.escalation_delivery_id` can adopt that execution as this
request's acceptance. A different incident waits; no active flow is ended or
modified. Once that flow ends, only the blocked recipient retries. This preserves
new danger reports instead of declaring an unrelated active call a success.

Connect/pool failures and 429 rejections retry with bounded backoff and Retry-After.
Read/write timeouts, 5xx responses, malformed acceptance responses, and crash windows
are uncertain: recent execution contexts can prove acceptance, but an absent result
**never authorizes a resend**. The search is bounded to one page, so an unmatched
request remains uncertain even if pagination or provider retention hides it.
There are at most five POST attempts and a ten-minute request lifetime, with up
to three assessment attempts. Exhausted/failed work emits an actionable error;
transient waiting is recorded separately. Tool responses explicitly prohibit
repeating uncertain/queued notifications and never claim a call was heard.

### Operation and rollout

The existing `TWILIO_ACCOUNT_SID`/`TWILIO_AUTH_TOKEN`, Studio flow, and caller ID
are reused. Obtain credentials from the existing Twilio account Console; do not
create or rotate keys to enable this change. Extra JSON flow parameters require
no published-flow edit. Read [Studio Execution](https://www.twilio.com/docs/studio/rest-api/v2/execution),
[Execution Context](https://www.twilio.com/docs/studio/rest-api/v2/execution-context),
and [429 semantics](https://www.twilio.com/docs/api/errors/20429).

Before rollout, run `./scripts/worktree-test.sh` against isolated local Postgres;
`test_escalation_delivery.py` uses synthetic data and `httpx.MockTransport` only.
Never replay a live emergency or use real SMS/calls as deployment smoke tests.
The existing production startup script applies migration `0032_escalation_outbox`
with `alembic upgrade head` before starting the server. Then verify `/api/health`, the deployed commit, scheduler registration, migration head,
and outbox/error telemetry using read-only checks. Historical notifications are
not inserted into this queue. Observe the next naturally arriving event for
end-to-end operational evidence; a healthy deploy alone does not prove delivery.

For an alert, inspect the request ID, recipient suffix, execution/conflict SID,
`last_error`, and immutable attempt identities. Query the three outbox tables
read-only. For `accepted`, use Twilio execution steps plus Calls/Messages resources
to distinguish provider status from a person's acknowledgment. Do not reset unknown
or accepted rows to pending. Resolve any uncertain send with provider evidence
before an operator authorizes another notification.

Rollback the application commit if needed and leave these additive tables intact.
Old code cannot recover queued work; inspect outstanding rows before rollback and
have an operator handle unresolved emergencies. Do not downgrade/drop production
outbox tables or replay their contents automatically.

## Quo credential configuration

All Portfolio Quo/OpenPhone clients use `QUO_API_KEY_PORTFOLIO`, including the
FastAPI backend, Sernia AI tools/triggers, ad hoc scripts, and the standalone
Sernia MCP service. This is the only supported API-key variable; there is no
fallback to `OPEN_PHONE_API_KEY` or `QUO_API_KEY`.

For recovery after the rename:

1. Set `QUO_API_KEY_PORTFOLIO` to the replacement credential in each FastAPI and
   Sernia MCP service environment that will be resumed (production, development,
   and any preview configuration that supplies Quo access).
2. For local use, set it independently in the root `.env` and
   `apps/sernia_mcp/.env`, or inject it into each service process. The MCP service
   does not inherit the root `.env`.
3. Remove `OPEN_PHONE_API_KEY` and `QUO_API_KEY` from those environments once the
   replacement is set. Do not restore revoked values under the new name.
4. Restart the affected services only when incident recovery is authorized so
   process-level clients and caches use the replacement. Verify with a read-only
   Quo lookup; do not use an external SMS send as a smoke test.

Webhook verification uses `QUO_WEBHOOK_SECRET`. Before resuming each FastAPI
backend, set that variable to its Quo endpoint signing secret, remove
`OPEN_PHONE_WEBHOOK_SECRET`, and restart when recovery is authorized. Apply the
same rename in the local root `.env` when needed. The old name has no fallback;
startup validation and HMAC verification both require the new name. This secret
is not needed by the standalone MCP service or frontend.

The `QUO_*_PHONE_ID` settings, webhook URL, signature header, and HMAC algorithm
are unchanged. These renames do not rotate or provision any credentials.

## Operational route authentication

`POST /api/open_phone/send_message` and
`POST /api/open_phone/create_contacts_in_openphone` require a Clerk-authenticated
user with a verified `@serniacapital.com` email, just like the contact lookup
route. Passwords in request bodies cannot authorize these operations. The legacy
password-only `DELETE /api/open_phone/contact/{id}` endpoint has been removed.
Quo webhook signature verification still uses `QUO_WEBHOOK_SECRET`.
