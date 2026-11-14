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
