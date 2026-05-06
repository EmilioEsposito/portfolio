_Reported by Claude Code on behalf of Emilio Esposito (account owner, OR98t1AGEk). Emilio asked me to investigate why the agent couldn't read group-thread history; I exhausted the documented `participants[]` API surface live against this account before drafting this report. Repro steps below were captured from real `curl` output during that session._

---

I found a bug on the messages API. Please forward directly to engineers — they should be able to fix it with this information.

**Bug**: `GET /v1/messages` silently drops group-thread messages.

When passing multiple `participants[]`, the API returns only the 1:1 conversation with the **first** participant — multi-recipient (group) messages are never included, regardless of array brackets, indexed `[0]/[1]`, repeated keys, or reversed order.

## Repro

Group conversation in our account:
- Conversation ID: `CNcfa69340d4974b68b18c17144e31041a`
- Participants: `+14583262115`, `+15037070359`

```bash
# Direct fetch by ID works — confirms the group msg exists
# (response shows to: ["+14583262115","+15037070359"]).
# <GROUP_MSG_ID> is the conversation's lastActivityId (an AC-prefixed
# 32-hex message ID, redacted here because GitHub's secret scanner
# false-positives it as a Twilio Account SID):
curl -H 'Authorization: <REDACTED>' \
  https://api.openphone.com/v1/messages/<GROUP_MSG_ID>

# Filter by both participants — count=0, that group msg is missing:
curl -H 'Authorization: <REDACTED>' \
  'https://api.openphone.com/v1/messages?phoneNumberId=PNpTZEJ7la&participants[]=%2B14583262115&participants[]=%2B15037070359&maxResults=50'
```

## Ask

Add either a `conversationId` filter on `/v1/messages` or a `GET /v1/conversations/{id}/messages` endpoint. Today, group thread messages are only reachable one-at-a-time via `lastActivityId` on the conversation object, which makes group thread history unreadable through the public API.
