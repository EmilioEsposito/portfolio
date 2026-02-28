# Web Push Notifications

PWA push notifications for Sernia AI's HITL (Human-in-the-Loop) approval flow. Alerts users on phones and desktops when a tool call needs approval — no native app required.

## Protocol

Uses the **W3C Push API** + **VAPID** (Voluntary Application Server Identification) authentication. This is completely separate from the Expo push module at `api/src/push/` which uses Expo's proprietary push service.

- **Spec**: [W3C Push API](https://www.w3.org/TR/push-api/), [RFC 8292 (VAPID)](https://datatracker.ietf.org/doc/html/rfc8292)
- **Python library**: [pywebpush](https://github.com/web-push-libs/pywebpush) — handles encryption (RFC 8291) and VAPID signing
- **Browser API**: `PushManager.subscribe()` → returns endpoint + encryption keys

### No external services required

Web Push is an open W3C standard built into browsers. **No Firebase, Google Cloud, Apple Developer account, or any third-party dashboard is needed.** Each browser vendor operates its own push service transparently:

- Chrome → Google's FCM (automatic, no setup)
- Safari → Apple's APNs (automatic, no setup)
- Firefox → Mozilla's autopush (automatic, no setup)

When a browser subscribes, it returns an endpoint URL on its vendor's push service. The backend just POSTs encrypted payloads to those URLs, signed with VAPID keys. The only infrastructure is your own Postgres (for storing subscriptions) and the `pywebpush` library.

## Architecture

```
Browser                          Backend                         Push Service
───────                          ───────                         ────────────
SW registers
  ↓
PushManager.subscribe()  ──→     POST /sernia-ai/push/subscribe
  (endpoint, p256dh, auth)       → saves to web_push_subscriptions table

                                 Agent produces DeferredToolRequests
                                 _on_complete callback fires
                                   ↓
                                 notify_pending_approval()  ──→  POST to push endpoint
                                   (pywebpush + VAPID)           (browser push service)
                                                                   ↓
SW receives push event ←─────────────────────────────────────── push delivered
  ↓
showNotification()
  ↓
User clicks → navigate to /sernia-chat?id=<conversation_id>
```

## Environment Variables

All three vars go on the **FastAPI service only** (both Railway and local `.env`). The React Router frontend needs **nothing** — it fetches the public key at runtime via `GET /api/sernia-ai/push/vapid-public-key`.

| Variable | Railway Service | Description |
|----------|----------------|-------------|
| `VAPID_PRIVATE_KEY` | FastAPI | PEM-encoded EC private key for signing push requests |
| `VAPID_PUBLIC_KEY` | FastAPI | URL-safe base64 public key (returned to browser via API) |
| `VAPID_CLAIM_EMAIL` | FastAPI | Contact email in `mailto:` format — included in VAPID headers so browser push services can reach you if your server misbehaves. Not verified. (default: `mailto:admin@serniacapital.com`) |

### Generating VAPID Keys

```bash
source .venv/bin/activate && python adhoc/create_push_keys.py
```

Output is in `.env`-ready format — copy directly into local `.env` and Railway FastAPI service env vars.

### Multi-environment notes

- **Use the same keypair** across local/dev/prod. VAPID keys are just a signing identity. Each environment's DB only stores subscriptions collected on that environment, so there's no cross-talk.
- **localhost vs deployed**: Different browser origins = completely separate push subscriptions. Subscribing on localhost won't affect prod.
- **Duplicate notifications**: You'll only get notified by environments where you clicked the bell icon. A conversation on prod doesn't exist on dev, so no duplicates.

## Files

| File | Purpose |
|------|---------|
| `models.py` | `WebPushSubscription` SQLAlchemy model — stores browser push endpoints |
| `service.py` | Subscription CRUD + push sending via `pywebpush` |
| `routes.py` | 3 endpoints: `GET /push/vapid-public-key`, `POST /push/subscribe`, `POST /push/unsubscribe` |
| `apps/web-react-router/public/sw.js` | Service worker — handles `push` + `notificationclick` events only (no caching) |
| `apps/web-react-router/public/manifest.json` | PWA manifest — enables "Add to Home Screen" on mobile |
| `apps/web-react-router/app/hooks/use-push-notifications.ts` | React hook for SW registration, permission, subscribe/unsubscribe |

## Endpoints

All under `/api/sernia-ai/push/`, gated by `_sernia_gate` (requires `@serniacapital.com` Clerk user).

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/push/vapid-public-key` | GET | Returns VAPID public key for `PushManager.subscribe()` |
| `/push/subscribe` | POST | Saves browser push subscription to DB |
| `/push/unsubscribe` | POST | Removes subscription |

## iOS Notes

- Web Push only works on **iOS Safari 16.4+** when installed as a **standalone PWA** (Add to Home Screen)
- The `manifest.json` + `apple-mobile-web-app-capable` meta tag in `root.tsx` enables this
- The React hook detects iOS + non-standalone → shows "Install app for notifications" hint

## Debugging

- **Chrome DevTools** → Application → Service Workers: verify `sw.js` registered
- **Chrome DevTools** → Application → Manifest: verify `manifest.json` detected
- **DB check**: `SELECT * FROM web_push_subscriptions;` to see active subscriptions
- **Push not arriving?**: Check `VAPID_PRIVATE_KEY` is set, check Logfire for `web push send error`
- **410 Gone**: Subscription expired (browser revoked) — auto-cleaned by `notify_all_sernia_users`
