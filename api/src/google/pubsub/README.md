# Gmail Pub/Sub processing

`POST /api/google/pubsub/gmail/notifications` verifies the signed Google identity,
exact audience, configured push service account and fixed mailbox before doing
any work. Notifications are wake-ups, not message payloads or safe sync cursors.

Google's [`history.list`](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.history/list)
returns records **after** `startHistoryId`. Querying from the notification's own
latest ID can omit the change that caused it. An empty successful response is a
valid no-change result, including immediately after a watch renewal; it must not
start a 20/40/80-second polling loop or be reported as upstream rate limiting.

## Progress and acknowledgments

The existing `app_settings` table stores the mailbox checkpoint under
`gmail_history_checkpoint:<mailbox>` with value `{"history_id":"<decimal ID>"}`.
There is no schema migration and no automatic initialization. Email rows' history
arrays are not complete processing checkpoints and must not be used as a shortcut.

For every authenticated notification, including old or duplicate deliveries:

1. Read the saved checkpoint. If absent, return 503 with
   `X-Retry-Reason: gmail_checkpoint_uninitialized`; do not infer it from the wake-up.
2. Fetch one page (at most 50 history records) starting after that checkpoint.
   Blocking Google calls run in a worker thread. Successful empty history returns
   immediately. Provider errors retain the checkpoint and log their HTTP status;
   an upstream 429 is distinct from a local processing failure.
3. Fetch/store every changed message and durably enqueue eligible Zillow emails
   using the existing idempotent queue and freshness policy. Unavailable messages returning 404 must first be recorded in the durable
   unresolved exception ledger. A 404 does not establish deletion or classification. A failed save or enqueue fails the page.
4. Only after the whole page succeeds, compare-and-swap the saved checkpoint.
   Concurrent deliveries may repeat idempotent work but cannot rewind progress.
   When `nextPageToken` exists, save the last **record** ID, not the mailbox's
   current head, and return 503 (`gmail_history_remaining`) to request redelivery.
   The next attempt resumes after that record. Only the final page may save the
   response's mailbox `historyId` and return 204. Valid no-change pages also return 204.

Processing failures return 503 (or 500 for partial/unhandled failures), never a
false acknowledgment. All non-2xx outcomes let Pub/Sub retry according to its
configured subscription policy. Neither a 499 client disconnect nor a failed
HTTP response alone proves data was dropped: email/queue writes may already have
committed, and retries are idempotent. The checkpoint remains conservative until
the page is complete. An invalid/expired history ID (Gmail 404) returns 503 with
`gmail_history_expired` and requires reconciliation; a newer notification cannot
repair an unknown gap.

Paging bounds record count, not total processing time. A large individual record
or slow provider may still exceed a push deadline and be retried. This change
removes deliberate 140-second idle waits; it does not replace the handler with a
durable background sync worker. If large pages repeatedly time out, inspect their
processing before changing deadlines or replaying work.

## Approval required before first rollout

This change deliberately fails closed without a reviewed initial checkpoint.
Do not deploy it alone and assume the first notification will initialize progress.
No production checkpoint, backfill, subscription setting or replay is changed by
this PR.

1. Read the actual subscription acknowledgment deadline, retry policy, retention
   and any dead-letter policy. Confirm queued notifications have not expired.
2. With explicit recovery approval, establish a trustworthy baseline: reconcile
   Gmail against stored message IDs over the possible gap (full sync if the old
   history has expired). Use storage-only reconciliation first; separately review
   any lead jobs that should be queued. Replaying notifications can enqueue lead
   automation, so it is not a read-only diagnostic action.
3. Select a valid history ID through which all required storage/queue work is
   confirmed complete. Never choose the latest ID merely to clear retries. Insert
   the dedicated `app_settings` row once, within an explicitly approved transaction,
   with that decimal ID as a JSON string. Refuse an existing row rather than overwrite
   its cursor. Preserve all unrelated settings. Coordinate this with deployment.
4. Authorize one drain/redelivery, verify page-by-page progress, a 204 for empty
   history, and persistence/queue outcomes. Watch renewal remains independent and
   must never move this processing checkpoint.

If rolling this code back, preserve the checkpoint row for investigation; the old
handler ignores it. Do not reset progress to a new notification or delete the row
to suppress errors. Such a rollback reinstates the old cursor and polling defects.

## Verification and telemetry

Tests cover empty responses, pagination past the notified ID, provider failures,
missing/expired checkpoints, concurrent compare-and-swap, failed database writes,
failed durable enqueues, identity boundaries, and credential-safe logging.

```sh
./scripts/worktree-test.sh api/src/tests/test_gmail_history_checkpoint.py api/src/tests/test_gmail_db_failures.py api/src/tests/test_pubsub_identity.py api/src/tests/test_pubsub_zillow_enqueue.py api/src/tests/test_pubsub_logging.py
```

Authentication logs record authorization presence and fixed outcomes, never
request headers, bearer-token prefixes, JWT claims or verifier exception text.


## Unavailable-message evidence and storage-only recovery

`app_settings` keys `gmail_unavailable:<mailbox>:<message_id>` retain an explicit
`unresolved` status, `unknown` classification, first recording time, last observed
time and an observations map. Each observation includes its source, HTTP status,
history range and the original per-message history/thread/event metadata. Original
incident log evidence can be added under separate keys. Atomic merges preserve
other observations and classification; repeated pages reuse observation keys.
A new 404 reopens an exception. Nothing automatically resolves or deletes it.
There is no schema migration: both ledger and checkpoint use the existing table.

A checkpoint means every message in that range was stored/queued under policy OR
accounted for by committed unresolved evidence. It does not certify unavailable
contents were processed or deleted. Ledger failure fails the page and prevents
advancement. Query the ledger independently of the cursor; advancing must never
remove its audit obligation. Operators should review unresolved records after
rollout and whenever unavailable-message warnings recur. No automatic replay or
resolution job is installed. Keep records until an explicit audited resolution.

`_process_gmail_history(..., storage_only=True)` is an internal, non-HTTP recovery
path: it stores retrievable content without appending notification IDs to history
arrays and never enqueues leads. Encountering a Zillow message fails the recovery
page after saving its content, requiring a separate lead decision before cursor
initialization. A previously unavailable Zillow message also cannot auto-enqueue
while its exception is unresolved, even on a normal delivery. Other future mail
retains the existing processing policy.

For first recovery, bound pages and provider reads, record original incident
observations, and stop on failed storage/evidence writes or any lead needing a
review. Only initialize after every page in the reviewed incident interval is
accounted for. Record the start/final history IDs, counts and unresolved IDs in a
separate recovery manifest, alongside the cursor. Do not equate that bounded
incident reconciliation with a full historical mailbox audit. Check remaining
exceptions separately after successful 204 responses and backlog reduction.
