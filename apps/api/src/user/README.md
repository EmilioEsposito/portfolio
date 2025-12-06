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

## Webhook Verification & Multi-Environment Handling

The `/api/user/webhook/clerk` endpoint performs crucial security verification using the `svix` library and signing secrets provided by Clerk.

**Important:** This setup is designed to handle webhooks from **both** Clerk Development and Production instances using a **single database table** and a single API endpoint.

1.  **Separate Secrets:** It expects two environment variables:
    *   `DEV_CLERK_WEBHOOK_SECRET`: The signing secret from your Clerk Development instance.
    *   `PROD_CLERK_WEBHOOK_SECRET`: The signing secret from your Clerk Production instance.
2.  **Verification Logic:** When a webhook arrives, the endpoint attempts verification first with the `DEV` secret. If that fails (specifically with a `WebhookVerificationError`), it then attempts verification with the `PROD` secret.
3.  **Environment Determination:** Based *solely* on which secret successfully verifies the webhook, the handler determines the source `environment` ("development" or "production").
4.  **Database Distinction:** The `clerk_user_id` (from the webhook payload's `data` field) and the derived `environment` string are stored in the `users` table. The combination of `clerk_user_id` and `environment` is enforced as unique, allowing user records originating from both Clerk instances to coexist within the same table without conflict.

This approach allows managing users from different Clerk environments within a unified database structure while maintaining data integrity.

