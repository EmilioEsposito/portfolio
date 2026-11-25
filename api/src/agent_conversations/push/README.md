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
notification and opens `/team/chat?id=…` (`team_links.chat_path`) when it is clicked.

Both agents use the same approval notifications.

### No push about a conversation its reader has open

Nobody gets a push (response, approval or alert) about a conversation they have
in front of them: an open phone app showing it, or a focused browser window
showing it on a computer. The chat view reports its conversation to
`POST /push/presence` on arrival and every 15 seconds while it is in front, and
reports leaving as soon as it is hidden, loses focus on a computer, switches
conversation or tab, or closes (`lib/conversation-presence.ts`). `presence.py`
keeps those reports in memory and forgets a page that stops reporting after 40
seconds; `notify_user_push` and `notify_all_sernia_users` leave out the readers
of the push's `conversation_id`. Teammates who do not have it open still get
approvals and alerts. Each report is numbered, so one handled late cannot undo a
newer one.

The push is held back on the server because a service worker cannot drop one on
Apple's push service (Home Screen apps on iPhone and iPad, Safari on a Mac):
WebKit counts every push that shows no notification within 30 seconds, never
resets the count, and deletes all of the site's push subscriptions on the third
(`maxSilentPushCount` in WebKit's `WebPushDaemonConstants.h`). The service worker
therefore always shows a push on Apple's service. Elsewhere it still hides a
response that arrives for the focused conversation, which only happens when a
push crosses the reader's arrival. Either way it tells the open page the
response is ready.

Presence lives in the API process's memory (one process today). A restart, or
a report that is lost, only means a push is sent as it was before this rule. General-agent SMS notification helpers live separately
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
- **Duplicate notifications**: You'll only get notified by environments where you turned notifications on. A conversation on prod doesn't exist on dev, so no duplicates.

## Files

| File | Purpose |
|------|---------|
| `models.py` | `WebPushSubscription` SQLAlchemy model — stores browser push endpoints |
| `service.py` | Subscription CRUD + targeted response push, and team-wide approval/alert push |
| `presence.py` | Which conversation each team page has in front of its reader; pushes skip those readers |
| `routes.py` | `GET /push/vapid-public-key`, `POST /push/subscribe`, `POST /push/unsubscribe`, `POST /push/test`, `POST /push/presence`, `GET /push/devices`, `GET /push/admin/registrations` |
| `apps/web-react-router/public/team/sw.js` | Service worker — shows pushes (hides a response for the focused conversation except on Apple's push service), the app icon's badge, and notification deeplinks (no caching) |
| `apps/web-react-router/public/team/manifest.json` | PWA manifest and its 192 and 512 pixel icons — lets a browser offer installing, and "Add to Home Screen" on iOS. The installed app opens the team home (`/team`); `launch_handler` makes a link the system hands to the installed app reuse its open window |
| `apps/web-react-router/app/sites/team/hooks/use-push-notifications.ts` | React hook for SW registration, permission, subscribe/unsubscribe. One instance for all team pages |
| `apps/web-react-router/app/sites/team/lib/conversation-presence.ts`, `hooks/use-conversation-presence.ts` | The chat view's presence reports: when it is in front, the heartbeat, and leaving |
| `apps/web-react-router/app/sites/team/components/team-device.tsx` | What every team page shows: the sidebar's Notifications row, the top-bar button while notifications need attention, the panel with the buttons, and the banner that asks; also clears the app icon's badge |
| `apps/web-react-router/app/sites/team/lib/team-device.ts` | What the device can do, where notifications stand on it, and which reminder to show (no React) |
| `apps/web-react-router/app/sites/team/lib/install-prompt.ts` | Keeps the browser's own install prompt (Chrome, Edge) until a page offers it |

## Endpoints

All under `/api/sernia-ai/push/`, gated by `_sernia_gate` (requires `@serniacapital.com` Clerk user).

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/push/vapid-public-key` | GET | Returns VAPID public key for `PushManager.subscribe()` |
| `/push/subscribe` | POST | Saves browser push subscription to DB |
| `/push/unsubscribe` | POST | Removes subscription |
| `/push/test` | POST | Sends a test notification to the caller's own devices, nobody else's |
| `/push/presence` | POST | `{client_id, conversation_id \| null, seq}`: the conversation this page has in front of the caller, or none. 204 |
| `/push/devices` | GET | `{"ios_app": bool}`: whether the caller has the Home Screen app of an iPhone or iPad subscribed (their own rows only) |

## What a team page shows

The sidebar of every team page has a Notifications row with a word: "On",
"Off", "Blocked", "App only" (in an iOS browser tab), or "Not available" where the browser
cannot notify. "Off" is amber and "Blocked" red; the rest are muted. The row
reports; it never switches anything. It opens a panel that says it in a
sentence, with a status light beside the word, and whose buttons are named for
what they do ("Turn on notifications", "Send a test", "Turn off", "Install
app").

When notifications are on there is nothing else: no dot, which would read as a
notification waiting. Only while they are off or blocked does a phone's top bar
(and the chat's header) show a small amber or red "Off" / "Blocked" button
with a crossed-out bell, which opens the same panel.

"On" needs both a subscription for the signed-in user and the browser's
permission; when the permission is changed in the browser's settings, the
subscription is checked again.

On push-capable browsers, a banner offers to turn on notifications or explains
how to unblock them. Once notifications are on, a phone whose browser offers
installing gets an “Install app” banner that opens the browser's own prompt.

On iOS 16.4 and later, a browser tab offers Home Screen install steps. The
tab cannot check the icon on this device. A push record can belong to a past
install or another device, so the tab does not look up account subscriptions.
The sidebar's Notifications row also opens the steps, even after the banner
is dismissed. Opening Sernia from its icon shows the app's own push status.

“Not now” snoozes a banner for one week. “Don't remind me” dismisses the
second offer indefinitely. Install and notification reminders are stored
separately in `sernia-team-reminders`. Turning notifications off in the
panel dismisses the notification reminder.

### Admin push devices

Emilio's verified primary account can open `/team/push-devices`. The API
(`GET /push/admin/registrations`) lists verified Sernia members from the
configured Clerk instance, including members with no registered endpoint.
Each registration shows its database ID, browser user agent, first registration
and last registration update. An Apple push endpoint and a mobile iOS user
agent indicate a Home Screen app at registration time. Older rows can have
identical timestamps because earlier upserts preserved the original timestamp.
Delivery time, current installation and a physical device identifier are
unavailable. Separate iOS installs can have separate endpoints. Repeated
registrations of one endpoint update the same row, and permanent push-service
errors retire invalid endpoints on send.

### The app icon's badge

The badge counts the notifications shown since the app was last opened and
still showing; opening the app clears it. Opening the app does not close its
notifications, so a team page that becomes visible clears the badge and tells
the service worker (`app-opened`), which keeps that moment in Cache Storage
and counts only notifications shown after it (each carries `shownAt` in its
data). The worker recounts after showing one, after a click, and on
`notificationclose` where the browser fires it. The Badging API exists in Home Screen apps on
iOS/iPadOS 16.4+ and installed apps in desktop Chrome and Edge; Chrome on
Android has none and shows its own dot while a notification is unread. Every
call is feature-detected and never throws.

## iOS Notes

- Web Push only works on **iOS Safari 16.4+** when installed as a **standalone PWA** (Add to Home Screen)
- Every push must show a notification: three that show none and WebKit deletes the site's subscriptions for good (see "No push about a conversation its reader has open")
- The `manifest.json` + `apple-mobile-web-app-capable` meta tag in `root.tsx` enables this
- An iPhone's browser tab has no `PushManager` at all, so iOS in a tab is checked for before push support: it is asked to use the Home Screen, never told its browser cannot notify
- iOS shows its permission prompt only while handling a tap, so the hook asks for permission first thing in the click
- An iPad's desktop-style user agent is recognized by its touch screen; iOS before 16.4 is told it cannot notify
- An iPhone or iPad keeps the address a Home Screen app was added with, so an app added before the start address became `/team` still opens the chat until it is removed and added again. Android and desktop installs pick up the new start address through the manifest's unchanged `id`
- iOS has no documented way to send links to a Home Screen web app, so links to serniacapital.com open in Safari there

## Debugging

- **Chrome DevTools** → Application → Service Workers: verify `/team/sw.js` registered
- **Chrome DevTools** → Application → Manifest: verify `/team/manifest.json` detected and installable
- **"Send a test"** in the notifications panel (sidebar → Notifications) pushes to the signed-in user's devices only
- **DB check**: `SELECT * FROM web_push_subscriptions;` to see active subscriptions
- **Push not arriving?**: Check `VAPID_PRIVATE_KEY` is set, check Logfire for `web push send error`
- **404/410, or VAPID key mismatch after a key rotation**: subscription can never succeed again — auto-cleaned by `notify_all_sernia_users` / `notify_user_push` (logged as `web push subscription expired` with a `reason`)
