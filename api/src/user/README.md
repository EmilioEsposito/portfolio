# User Module

This module is responsible for synchronizing user data from Clerk authentication into the application's database.

## Overview

Clerk provides webhooks for user events (`user.created`, `user.updated`, `user.deleted`). This module implements an endpoint to receive these webhooks, verifies their authenticity, and updates a local `users` table accordingly.

## Components

*   **`models.py`**: Defines the `User` SQLAlchemy model, which maps to the `users` table in the database.
    *   Uses a UUID (`id`) as the internal primary key.
    *   Stores the Clerk User ID (`clerk_user_id`).
    *   Includes basic user profile fields (email, name, image URL).
    *   Stores the `environment` ("development" or "production") derived purely from which Clerk webhook secret successfully verified the request.
    *   Contains a `raw_payload` field (JSON) to store the original data from the Clerk event's `data` field.
*   **`service.py`**: Contains the business logic for database operations:
    *   `upsert_user`: Creates a new user record or updates an existing one based on the event data and the derived `environment`.
    *   `delete_user`: Deletes a user record based on the event data and the derived `environment`.
*   **`routes.py`**: Defines the FastAPI endpoint:
    *   `POST /api/user/webhook/clerk`: Receives incoming webhook requests from Clerk.

## Webhook verification and instance configuration

`POST /api/user/webhook/clerk` verifies every request with Svix using the single
`CLERK_WEBHOOK_SECRET` environment variable. Missing configuration returns 500;
missing signature headers or invalid signatures return 400 before database writes.

Each deployment accepts one Clerk instance. Set `CLERK_WEBHOOK_SECRET` to the
endpoint signing secret from that instance's Clerk dashboard, and ensure
`CLERK_SECRET_KEY` belongs to the same instance. The latter's `sk_test_` prefix
labels user records `development`; `sk_live_` labels them `production`. An
unrecognized prefix fails closed. Hosting environment names do not determine the
Clerk instance type. The existing `(clerk_user_id, environment)` uniqueness and
user create/update/delete behavior remain unchanged.

### Migration

Before resuming each backend, set `CLERK_WEBHOOK_SECRET` to its intended Clerk
endpoint's signing secret (development for dev/local/preview, production for prod,
unless deliberately configured otherwise). Remove `DEV_CLERK_WEBHOOK_SECRET` and
`PROD_CLERK_WEBHOOK_SECRET`; neither is read as a fallback. Apply the same rename
in the local root `.env` when needed. Restart only when recovery is authorized.
Do not put the webhook secret in frontend or standalone MCP configuration.

A single backend no longer accepts both Clerk instances' webhook signatures.
Route each instance's webhook endpoint to the backend configured for that instance.
No credential values, provider configuration, or database rows are changed by
this code migration.
