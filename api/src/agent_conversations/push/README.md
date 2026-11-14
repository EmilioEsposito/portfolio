# Conversation Web Push

Browser notifications for Sernia's general and Zillow agents. Response-ready
notifications target the requesting user; approval requests and trigger alerts
notify the team. The API remains at `/api/sernia-ai/push/`.

Normal response notifications show up to 180 characters of whitespace-normalized
text, or “Your response is ready.” A user without an active subscription receives
no push; a targeted response never falls back to a team broadcast. Previews may
appear on a device's lock screen.

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

## Delivery

The browser subscribes through the W3C Push API and saves its endpoint and keys
through the authenticated API. `service.py` encrypts notifications with VAPID and
sends them to the browser vendor's push service. The service worker displays the
notification and opens `/sernia-chat?id=…` when it is clicked.

For web-chat completions, the service worker suppresses the OS notification
when the matching conversation is already focused. Both agents use the same
approval notifications. General-agent SMS notification helpers live separately
in [`sernia_ai/notifications.py`](../../sernia_ai/notifications.py).

## Environment Variables

All three vars go on the **FastAPI service only** (both Railway and local `.env`). The React Router frontend needs **nothing** — it fetches the public key at runtime via `GET /api/sernia-ai/push/vapid-public-key`.

| Variable | Railway Service | Description |
|----------|----------------|-------------|
| `VAPID_PRIVATE_KEY` | FastAPI | PEM-encoded EC private key for signing push requests |
| `VAPID_PUBLIC_KEY` | FastAPI | URL-safe base64 public key (returned to browser via API) |
| `VAPID_CLAIM_EMAIL` | FastAPI | Contact email in `mailto:` format — included in VAPID headers so browser push services can reach you if your server misbehaves. Not verified. (default: `mailto:admin@serniacapital.com`) |

### Generating VAPID Keys

After rotating the VAPID pair, returning signed-in browsers compare their existing
subscription's `applicationServerKey` with the current server public key. A stale
subscription is unsubscribed and replaced using the existing notification
permission. If the browser requires a user gesture, the UI shows notifications as
disabled so the user can enable them again. Offline devices must revisit the app;
server-side key rotation alone cannot renew their browser subscriptions.
Until they do, push services reject sends to the old subscriptions (Apple:
`400 VapidPkHashMismatch`; FCM: `403 ... do not correspond to the credentials`).
The send loop treats these like `404`/`410` and deletes the stored row, so a
rotation produces at most one failed send per stale device instead of a
recurring `web push send error`.

```bash
source .venv/bin/activate && python adhoc/create_push_keys.py
```

Output is in `.env`-ready format — copy directly into local `.env` and Railway FastAPI service env vars.

### Multi-environment notes

- **Use separate keypairs per environment** during recovery to limit exposure across environments. Keep each private/public pair together and renew browser subscriptions after changing it. For private capture without printing keys, use the [credential recovery playbook](../../../../docs/credentials/README.md); do not run the legacy print-to-terminal generator in an agent session.
- **localhost vs deployed**: Different browser origins = completely separate push subscriptions. Subscribing on localhost won't affect prod.
- **Duplicate notifications**: You'll only get notified by environments where you clicked the bell icon. A conversation on prod doesn't exist on dev, so no duplicates.

## Files

| File | Purpose |
|------|---------|
| `models.py` | `WebPushSubscription` SQLAlchemy model — stores browser push endpoints |
| `service.py` | Subscription CRUD + targeted response push, and team-wide approval/alert push |
| `routes.py` | 3 endpoints: `GET /push/vapid-public-key`, `POST /push/subscribe`, `POST /push/unsubscribe` |
| `apps/web-react-router/public/sw.js` | Service worker — handles focus-aware push display and notification deeplinks (no caching) |
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
- **404/410, or VAPID key mismatch after a key rotation**: subscription can never succeed again — auto-cleaned by `notify_all_sernia_users` / `notify_user_push` (logged as `web push subscription expired` with a `reason`)
