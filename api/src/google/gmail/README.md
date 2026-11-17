# Gmail integration

This module owns Gmail service access, stored messages, and watch management.
Authenticated Pub/Sub ingestion lives in [`../pubsub`](../pubsub/README.md);
inbound Zillow messages go through the durable queue to the dedicated
[Zillow Leads Agent](../../zillow_leads/README.md).

The POST `/watch/start`, `/watch/stop`, and `/watch/refresh` routes require
`Authorization: Bearer <CRON_SECRET>` and no request body. Missing, empty, or
whitespace-only server secrets reject every request with 401. Password bodies
cannot authorize these operations.

The unrelated fictional email studio was retired. Its `get_zillow_emails` and
`generate_email_response` endpoints, demo data, and request schema were removed;
the old public page URLs redirect to the lead-agent architecture. No operational
mailbox, watch, or lead-reply behavior changed as part of that removal.

`content.py` owns deterministic HTML/Zillow/quoted-reply cleanup; `threading.py`
owns reply headers and Gmail scopes. Both agents use these integration helpers.
General-agent tools remain in `sernia_ai/tools/google_tools.py`.

## Watch renewal and Railway cron

Renew with `users.watch` directly; never call `users.stop` first. Google recommends
[daily renewal](https://developers.google.com/workspace/gmail/api/guides/push#renewing_mailbox_watch).
A failed renewal therefore leaves any still-active subscription in place (an earlier
expiration alone does not prove coverage after a prior stop). Successful responses
include `expiration` and `historyId`. A watch also emits an immediate Pub/Sub notification.
The existing mailbox, topic, label filters, and delegation scopes remain unchanged.

`setup_gmail_watch` uses the Google client's randomized exponential backoff with
three retries (four attempts) for transient HTTP/transport errors; permanent errors
are not retried by the Google client. Start/refresh run in a worker thread so retries
do not block the API event loop. Exhaustion returns HTTP 500, never a success result.
The explicit stop endpoint returns HTTP 500 if the service reports `False`.

The versioned Railway Function entrypoint is
[`scripts/gmail-watch-refresh.mjs`](../../../../scripts/gmail-watch-refresh.mjs).
It runs under Bun or Node, requires `CUSTOM_RAILWAY_BACKEND_URL` and `CRON_SECRET`,
and retries network/timeouts, 408, 429 and 5xx at most three times total, waiting
2 then 4 seconds. Each HTTP attempt has a five-minute timeout to allow the backend's
Google retries to finish. Other HTTP errors, invalid responses and `success: false`
fail immediately. Failures exit 1; only validated future expiration/historyId is
logged as renewal. Hosted development skips are logged explicitly. Response bodies,
credentials and fetch exception text are not logged. With both layers, a cron run
can issue at most twelve watch attempts; the cron's total timeout budget is about
15 minutes plus six seconds. This is bounded and finishes well before the next run.

Deployment/configuration requires approval; merging code alone does not update a
Railway Function's inline source. After the backend fix is deployed, replace the
**production `cron-daily-gmail-watch-refresh` Function source** with the exact contents
of the versioned `.mjs` entrypoint (valid JavaScript in the existing Bun runtime).
Keep the current `0 0 * * *` UTC schedule, variables and `NEVER` restart policy;
the runner owns the bounded retries and exits nonzero after exhaustion. Do not
apply unrelated staged Railway changes. Verify a run logs a future expiration and
exits 0; a failed run must exit 1. No Railway configuration is changed by this PR.

Offline checks:

```sh
./scripts/worktree-test.sh api/src/tests/test_gmail_watch.py api/src/tests/test_pubsub_identity.py
./scripts/worktree-run.sh node --test scripts/tests/gmail-watch-refresh.test.mjs
```
