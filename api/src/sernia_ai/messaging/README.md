# Fixed external messages

`list_message_templates` and `send_templated_message` are available in both Sernia AI
and the standalone Sernia MCP server. They deliberately skip per-send HITL for a
small **code-owned** catalog. Ordinary free-form SMS/email approval gates are unchanged.

```json
{"template_id":"lease_end_mail_forwarding","contact_id":"CT_existing_contact_id","delivery":"both"}
```

`delivery` must be `sms`, `email`, or `both`. The initial template sends exactly:

> Automated reminder: Your lease end date has been detected to be within 2 weeks. Please setup mail-forwarding with USPS at https://moversguide.usps.com/. Thank you!

Email subject: `Automated reminder: USPS mail forwarding`. Email uses the shared
`all@serniacapital.com` mailbox and From address; SMS uses `QUO_SHARED_EXTERNAL_PHONE_ID`.
Each call targets one contact, in separate one-to-one channels, never a group or CC list.

## Eligibility and boundary

The tool fetches `/v1/contacts/{contact_id}` fresh from Quo, bypassing the contact
list cache. `Lease Start Date` must be on/before today; `Lease End Date` must be
0–14 calendar days away (inclusive), using America/New_York. Missing, conflicting,
or invalid dates block. Each selected channel must have exactly one unique valid
stored destination. Multiple addresses are rejected instead of guessing. All selected
destinations are validated before either channel sends. No caller-supplied dates,
addresses, message text, subject, sender, HTML, attachments, CC/BCC, or threading.

Templates and eligibility live in deployed Python, outside the agent filesystem
sandbox. The canonical policy is `templates.py`; MCP vendors it verbatim as
`core/message_templates.py` because it deploys independently. A parity test prevents
drift. Catalog entries are frozen, with no runtime registration or editing tools.
Contact names and custom field text cannot enter the outbound message.

## Discovery and operation

The catalog returns exact text plus usage guidance. Sernia's static instructions
and MCP's `sernia_context` both direct agents to it. This is the built-in playbook;
an editable knowledge skill is not needed for discovery and cannot authorize new
content. Before sending, inspect communication history and contact preferences.
Send once per lease; use both channels only when dual delivery is intended.

Results are per-channel: `accepted` means the provider accepted the request, not
that the tenant received it. A provider exception returns `unknown`, preserves the
other channel's result, and does not automatically repeat the send. Reconcile provider
history before any retry. A failed contact lookup or validation sends nothing.

There is **no durable duplicate-send ledger**: retries across calls, processes, or
services can duplicate this fixed reminder. The once-per-lease rule is agent guidance,
not a server-enforced guarantee. This tool does not install a scheduled campaign.
Delivery is one immediate, explicitly invoked operation. No live external tests.

## Dependencies and troubleshooting

Uses existing Quo (`OPEN_PHONE_API_KEY` in Sernia AI, `QUO_API_KEY` in MCP,
and `QUO_SHARED_EXTERNAL_PHONE_ID`) and Gmail domain-wide
delegation credentials. No new service, database, migration, or key is required.
See [Quo tools](../tools/README.md) and the [Google integration](../../google/README.md)
for credential setup; MCP has independent credentials documented in its README.
Provider references: [Quo API](https://www.quo.com/docs),
[Gmail sending](https://developers.google.com/workspace/gmail/api/guides/sending),
[domain-wide delegation](https://developers.google.com/identity/protocols/oauth2/service-account#delegatingauthority).
If blocked, fix verified contact data through the ordinary approved contact workflow;
do not fabricate dates or destinations to force eligibility. If `unknown`, inspect
Quo/Gmail history and Logfire's `templated external message result` before retrying.

To add another template, add its Literal ID, frozen catalog entry, explicit eligibility
policy, and tests in code; sync the vendored copy and deploy both services. Never add
format strings, model-authored substitutions, or a workspace template loader.
