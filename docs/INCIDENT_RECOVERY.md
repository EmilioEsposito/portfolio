# Portfolio credential incident — October 2026

Owner: Portfolio recovery chat. Ventures services have a separate owner and tracker.
This is a public-safe working checklist, not a forensic report or a declaration of
containment. Exact credential identities, account mappings, audit evidence, user
reports, replacement custody, and residual access live in the private ledger.
Public status deliberately does not expose which exact live credential remains
usable. Use the [playbook](CREDENTIAL_RECOVERY.md) for the execution procedure.

## Current boundary

- Preparation and local credential staging are authorized. Provider minting is
  delegated when exact ownership/scope and private capture are established.
- On October 22 the owner explicitly authorized Portfolio Railway recovery,
  credential delivery, outstanding revocations, integration restoration, and
  redeployment. This supersedes the earlier hold; it is an owner decision, not
  a claim that Railway has issued a security all-clear. Stage dev first and verify
  each exact credential, destination, deployment and runtime outcome.
- Owner previously removed the Railway GitHub app. Reconnection is now in scope,
  using only needed repository grants; browser authorization may require the owner.
  Direct workflow tokens remain independent access paths.
- Branch push is authorized. Review provisioning side effects before PR opening
  or merge; do not use a PR merely to bypass deliberate recovery sequencing.
  No live preview has yet been created for this branch.
- No retired Prefect/DBOS integration or legacy admin-password/Picker credential
  should be recreated. Their source cleanup is already on main; runtime removal
  must be verified after deployment.

## Preparation checkpoint (historical, before delivery)

| Work | State | Evidence / next step |
|---|---|---|
| Reconcile source and prior incident notes | Prepared | Canonical names use merged #333–#336; owner exports imported into private custody. |
| Private credential store | Prepared | Primary checkout ignored `.local/credential-recovery/`, owned 0700 directories / 0600 files; no values in agent output. |
| App-owned replacements | Locally staged only | Independent development/production session and cron candidates; development MCP bearer candidate. No activation or old-value invalidation. |
| Password-manager replacements | Imported | Development, production and local exports captured; corrected local database selection using fresh Neon metadata. |
| Selected delivery envelopes | Prepared offline | App-owned secrets split by environment and service; no network implementation or upload. |
| Audit visibility | Configuration improved | Cloud Data Access defaults enabled; Workspace logging limitations remain separate. Private investigation record retained. |
| Offline security checks | Passed | 15 synthetic tests plus Ruff lint/format; custody, leakage, parsing, routing, conflicting values, locks, paired keys, shared main-checkout storage and Git exclusion checks. No provider readiness claim. |
| Repeat rotation | Prepared | New cycle labels isolate future replacements; never copy old values forward implicitly. |
| Provider-specific mint/upload adapters | In progress | Custody framework is ready; adapters require exact identity, current API, private capture and uncertainty-journal tests. |

## Recovery execution checkpoint — October 22

- Railway plugin and CLI identity verified; Neon project access and Google Cloud
  project access verified. No current reauthentication blocker for those paths.
- Owner exports are imported. API/web local files have private backups and verified
  replacement readback. Local uses the separate Neon local branch.
- New database logins verified; development and production had no other client
  sessions during the bounded check. ClickUp, GitHub, Quo, Twilio and OpenRouter
  replacements passed harmless authentication checks. Clerk keys match exact instances.
- Google excessive Cloud IAM grants removed; replacement key minted into private
  custody. Gmail profile, Drive, Calendar and Sheets metadata reads succeeded with
  the reduced privileges. Both Clerk Google provider configurations received the
  replacement OAuth secret; fresh production Google sign-in and protected app
  loading succeeded after revoking all pre-recovery Clerk sessions.
- Development API/web and shared credentials delivered with readback; no deployment
  triggered by delivery. Retired API values cleared and scheduling held off.
- Quo dashboard confirms one enabled production subscription and no development
  subscription. The API listing omits this dashboard-managed subscription.
- Development and production API deployments succeeded; migrations and health
  checks passed. Production Gmail notifications now return successful responses.
  Web deployments also succeeded; development MCP is healthy with authenticated
  initialization verified and unauthenticated initialization rejected.
- Old database passwords and old Clerk, ClickUp, GitHub, Quo, Twilio and OpenRouter
  credentials reject authentication in both hosted environments.
- Seed bucket credentials from the provider reset were privately captured, bucket
  HEAD passed, and four GitHub Actions settings were accepted. No objects changed.
- API/web deployments in both environments and development MCP succeeded with
  the patched dependency release. The existing separate resume service remains
  available. Retired demo jobs remain offline; the production Gmail renewal job
  is tracked separately from the API's in-process scheduler.
- Production has one existing staged removal of the retired direct Anthropic key.
  This has been inspected; no broad staged patch has been applied.
- One development seed bucket and one unattached production legacy database volume
  were inventoried. Preserve the volume; the active database provider is Neon.
- Development/production replacement database logins succeeded and old passwords
  failed. The separate local branch also received a fresh password after detecting
  that its saved password still matched the pre-recovery local configuration;
  new login succeeded, old login failed and no other client sessions remained.
  Local API/web/MCP files received applicable replacements with private backups.

## Credential and environment checklist

`D` = development, `P` = production, `L` = local. Service names are source-derived
candidate consumers, not fresh Railway inventory. Per-environment issuer revocation,
capture, provider verification, delivery and runtime evidence stay independent in
the private ledger. A replacement saved by the owner is not deployment evidence.

| ID | Environment / consumers | Credential(s) | Revoke or rotate at | Preparation / cutover obligation |
|---|---|---|---|---|
| AUTH-01 | D/P/L API + web | `CLERK_SECRET_KEY` | Exact Clerk instance | Import owner's replacements; verify instance and key mode; deliver to both services. Assess sessions/connected OAuth grants separately. |
| AUTH-02 | D/P API | `CLERK_WEBHOOK_SECRET` | Exact Clerk/Svix endpoint | Import per-environment values; coordinate endpoint overlap and handler verification. |
| AUTH-03 | D MCP | `FASTMCP_SERVER_AUTH_CLERK_CLIENT_SECRET` | Clerk OAuth app | Import existing replacement; preserve client ID/domain/redirects; assess cached OAuth grants. Production placement not established. |
| AUTH-04 | D MCP + confirmed callers | `SERNIA_MCP_INTERNAL_BEARER_TOKEN` | All accepting services/callers | Candidate staged; verify actual callers and rotate together before enabling endpoint. |
| AUTH-05 | D/P/L API | `SESSION_SECRET_KEY` | Runtime configuration | Candidates staged for D/P; sessions signed with old material must expire/invalidate at cutover. |
| AUTH-06 | D/P/L API + job callers | `CRON_SECRET` | Runtime and callers | Candidates staged for D/P; verify bearer-only protected routes and update callers together. |
| DB-01 | D/P/L API | `DATABASE_URL`, `DATABASE_URL_UNPOOLED` | Exact Neon branch/SQL role | Import existing replacements; pair-check; verify endpoint ownership, new login, old rejection and surviving sessions. Do not infer branch from local `.env`. |
| GOOGLE-01 | D/P/L API; Clerk provider config | `GOOGLE_OAUTH_CLIENT_SECRET` | Exact Google OAuth client | Import owner's replacement; update both Clerk Google provider configurations plus backend consumers after scope review. Preserve callbacks. |
| GOOGLE-02 | API + D MCP | `GOOGLE_SERVICE_ACCOUNT_CREDENTIALS` | Google IAM service-account keys | Containment/restoration decision separate from minting. Review excessive roles and domain-wide delegation first; preserve disabled state during prep. |
| MSG-01 | API + D MCP | `QUO_API_KEY_PORTFOLIO` | Quo API-key issuer | Import existing replacement; match account/key and validate harmless read only. No test messages. |
| MSG-02 | D/P API | `QUO_WEBHOOK_SECRET` | Quo webhook endpoint | Import existing replacement; exact endpoint/consumer pair, no duplicate webhook creation. |
| MSG-03 | D/P/L API | `TWILIO_AUTH_TOKEN` | Exact Twilio account/subaccount | Import replacement; verify account, promoted/secondary-token state and webhook dependencies without calls/SMS. |
| TASK-01 | API + D MCP | `CLICKUP_API_KEY` | ClickUp owner/token issuer | Import existing replacement; match owner/workspace and both consumers. |
| CODE-01 | API + D MCP workspace sync | `GITHUB_EMILIO_PERSONAL_WRITE_PAT` | GitHub token issuer | Import existing replacement; least-privilege knowledge-repo scope; cached clones and other app authorizations are independent. |
| AI-01 | API operations | `PORTFOLIO_OPENROUTER_API_KEY` | OpenRouter | Import existing replacement; verify cap/account; user reports sharing D/P, not isolation. |
| AI-02 | API public demos | `PUBLIC_PORTFOLIO_OPENROUTER_API_KEY` | OpenRouter | Import existing capped replacement; keep separate from operations; no frontend exposure. |
| AI-03 | Historical direct-provider consumers | `OPENAI_API_KEY`, `SERNIA_ANTHROPIC_API_KEY`, stale aliases | OpenAI/Anthropic | Reconcile issuer revocation and actual remaining consumers; do not create new direct-provider keys merely to fill old variables. Excluded from delivery manifest pending need. |
| OBS-01 | API | `LOGFIRE_TOKEN` | Logfire project tokens | Import owner's replacement before creating another; independent scope/ingestion proof and deliberate old-token retirement. Preserve telemetry history. |
| OBS-02 | Provider alert integration, if used | `SLACK_WEBHOOK_LOGFIRE` | Slack app/webhook issuer | Establish actual integration and ownership; replace only if still used. Excluded from automatic delivery pending mapping. |
| PUSH-01 | D/P API + browser subscriptions | `VAPID_PRIVATE_KEY`, `VAPID_PUBLIC_KEY` | App-generated pair | Owner reports replacement pairs already generated; import and verify pairing. Plan browser resubscription before retiring old push identity. |
| CI-01 | GitHub Actions | `NEON_API_KEY`, `RAILWAY_GHA_TOKEN`, `LOGFIRE_GHA_TOKEN`, `CC_ROUTINE_TOKEN` | Respective issuers + GitHub secrets | Determine exact scopes/IDs and exposure path; coordinate shared admin keys; never infer hosted exposure from a workflow reference alone. |
| CI-02 | Seed fixture consumers | `SEED_BUCKET_ACCESS_KEY_ID`, `SEED_BUCKET_SECRET_ACCESS_KEY` | Bucket credential issuer | Keep held; bucket reconnection, new credential capture and consumer delivery separate. No seed download during prep. |
| CI-03 | Any private registry consumers | Registry credentials | Exact registry issuer | Conditional inventory; none established by source-only review. Do not fabricate replacements. |
| RET-01 | Historical configuration | Admin password hash/salt, Picker key, Prefect, DBOS | Relevant issuer / configuration removal | Do not restore. Source removal does not prove old deployed auth paths are gone. |

Public configuration such as Clerk publishable keys, OAuth client IDs, Twilio account
SID, VAPID public key, callback URLs, service URLs and model switches is not a secret
requiring arbitrary rotation. It still needs exact environment mapping. The offline
manifest accepts required companion fields but never grants them secret status.

## Actions beyond key rotation

- [ ] Import owner exports and reconcile every variable against exact environment,
  account, issuer ID, expiry/scope and expected service; mark unknowns explicitly.
- [ ] Verify issuer revocation independently where safe; retain original evidence
  classification and recorded exceptions privately. Never reactivate an old key.
- [ ] Review surviving Clerk sessions, Google grants/delegation, MCP OAuth grants,
  database sessions, GitHub authorizations and credentials an old credential could
  have created. Key rotation alone does not settle these.
- [ ] Confirm database backups/retention/other branches and any old token copies;
  preserve restore ability. Do not purge history or reset application data by default.
- [ ] Keep the Google audit review's bounded conclusion and logging gaps; absence
  of matching events is not proof of no exfiltration. Preserve evidence privately.
- [ ] Reconcile repository/workflow/app access before restoring CI or GitHub integration.
- [ ] Build a provider adapter only when it adds value beyond importing already saved
  replacements; require private capture and uncertain-outcome reconciliation.
- [ ] At authorized cutover, validate names and targets against fresh metadata,
  deliver selected values with deployment suppressed, then independently read back.
- [ ] Deploy reviewed source with background automation disabled; verify owner login,
  Clerk Google configuration, webhook signatures, cron protection, tool authorization,
  and retired auth-path removal. No external messages/calls as routine QA.
- [ ] Renew web-push subscriptions deliberately and activate each integration and
  schedule only after controlled verification.
- [ ] Perform final old-key rejection/residual-access checks; record final provider,
  deployment and runtime evidence separately, then close the incident.

## References

- [Railway security disclosure](https://blog.railway.com/p/security-disclosure-wednesday-october-21st)
- [Public incident status](https://status.railway.com/incident/QH8L8B3E)
- [Companion Ventures approach, reference only](https://github.com/sernia-ventures/ventures/pull/437)
- [Google Data Access logging configuration](https://docs.cloud.google.com/logging/docs/audit/configure-data-access)
- [Provider/custody playbook](CREDENTIAL_RECOVERY.md)
- [Background automation policy](BACKGROUND_AUTOMATION.md)

## Residual-access verification

- All pre-recovery Clerk sessions revoked in both instances; fresh production
  Google sign-in and protected application loading verified afterward.
- Old MCP OAuth client secret rejected by Clerk introspection; replacement accepted.
  Exact deployed FastMCP source derives proxy JWT signing and encrypted token-store
  keys from the rotated client secret, invalidating previous proxy session material.
  Unauthenticated initialization rejected; replacement bearer initialization passed.
- Google project has only the expected app service account and one user-managed key
  (the privately captured replacement). Workspace delegation remains enabled; Cloud
  Editor and project-wide Service Account Token Creator removed. This is not removal
  of Gmail/Drive/Calendar/Sheets delegation.
  Application source uses the account's own private-key credentials and Workspace
  `with_subject` delegation, not Cloud IAM impersonation of other service accounts.
  Successful metadata reads verified the intended Google access after cleanup.
- Retained old Logfire ingestion token disabled; new-token ingestion observed.
  Old direct OpenAI/Anthropic keys also reject authentication.
- Hosted database login roles are only the app owner and provider-managed roles;
  no event triggers observed. Preserved private scheduler snapshots and removed
  known recurring serialized jobs for regeneration from reviewed source, without
  unpickling database-controlled state. Expired OAuth copies without refresh tokens
  cleared from development/local; production credential table was already empty.
- Bounded knowledge-repository review found only expected memory-file edits; added
  URL hosts were the app and ClickUp. This is not a comprehensive forensic guarantee.
- Critical dependency fixes deployed successfully: certifi 2026.10.19 in both
  Python locks and proxy-addr 2.0.8 with an IP-spoofing regression test. This does
  not claim that every pre-existing dependency advisory has been resolved.
- Normal production background automation resumed after removing the old
  serialized recurring jobs; fresh scheduler startup was observed in telemetry.
  Development and local background automation remain disabled.
- Production Gmail-watch renewal service redeployed successfully with the rotated
  shared cron credential and its production private-network destination verified.
  A controlled refresh returned HTTP 200 and a new watch expiration. The next
  scheduled invocation remains a separate observation from this successful refresh.
- Twenty synthetic custody/delivery/Neon-rotation tests passed; the patched runtime
  release also passed the full CI test jobs. No external test messages were sent.
- Natural production Quo webhook deliveries returned HTTP 200 after rotation;
  bounded telemetry readback found no HTTP 5xx responses in the checked window.

PR #337 merged after the recovery workflow completed successfully. The primary
checkout was fast-forwarded while preserving its unrelated workspace edit and
private configuration; local API/MCP/web dependencies were synchronized to the
patched locks. The merge-triggered production deployment is a separate final
release observation from the successful patched deployments above.

Account-wide credentials not observed on
Railway are tracked separately; their mere presence in GitHub Actions is not evidence
of exposure. Browser push subscribers must resubscribe for the new VAPID identity.
