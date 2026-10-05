# Background automation policy

`BACKGROUND_AUTOMATION_ENABLED` controls autonomous scheduler startup, recurring jobs and
startup catch-up. Missing means false. Production is explicitly true; development, previews
and local development default false. Requests, webhooks, manual actions and their bounded
background work remain available. Scheduled actions may be created while automation is off,
but will not execute automatically until the scheduler is enabled.

`scripts/railway_automation_policy.json` owns the hosted defaults. The reconciliation workflow
applies changes to the FastAPI service only, preserves unrelated variables, and redeploys changed
services. PR provisioning explicitly applies the preview default, even if the source environment
had a temporary opt-in. The existing Railway build/deploy configuration remains unchanged.

To test schedules temporarily, set `BACKGROUND_AUTOMATION_ENABLED=true` on the exact development
or PR FastAPI service and redeploy. Normal recurring work and startup catch-up can now run using
that environment's integrations. Set false and redeploy afterward. There is no automatic expiry.
A policy reconciliation or PR update resets the override to the committed default. Make durable
exceptions in the policy file and revert them after the test. Local use sets the same variable
through the normal development launcher and requires a restart.

For new schedules, use `api.src.utils.automation.background_automation_enabled` at scheduler
registration/startup, not in request or webhook handlers. Do not add another environment-policy
toggle. Dedicated external cron services are separate workloads, not controlled by this API
scheduler switch; review them explicitly before claiming an environment is fully idle.
