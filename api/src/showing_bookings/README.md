# Showing bookings

Booking in-person showings of Sernia Capital's rental units: who can host a
showing, the showings booked, and every text or calendar change they owe. The
work is tracked in [issue #368](https://github.com/EmilioEsposito/portfolio/issues/368)
and lands one pull request at a time, each with sending switched off or in
capture mode.

The steps so far hold the tables, the notice outbox, the test harness, the
calendar connections, the showing agents, the open times, the public
booking API and cancelling. Nothing is sent unless switched on: the setting starts in capture
mode, outside production only test recipients can ever be sent to, and the
public booking API is off in production until the team opens it.

## Tables

| Table | One row is | Example |
| --- | --- | --- |
| `calendar_connections` | a Google account that has granted calendar access | the team account, connected by a team member, last read fine |
| `showing_agents` | a person who can host showings | Jane Doe, busy when her calendar is busy, hours weekdays 4 to 7 PM, priority 1 |
| `showings` | one unit at one start time with one host | unit `320-03`, Wed 4:00 to 4:30 PM, Jane, up to 4 parties, scheduled |
| `showing_attendees` | one prospect in one showing | Alex, mobile and email, texts accepted with the wording version, both requirement ticks confirmed, booked on the page |
| `showing_notices` | one text or calendar change a booking owes | the prospect's confirmation text for that showing, captured |

Listings and the showing rules stay in `rental_listings`. Agents' weekly hours
and preferred windows use the listings' `PreferredWindow` shape, where `end` is
the last start time.

The database enforces three rules, so no code path can break them:

- One scheduled showing per unit and start time (a partial unique index). A
  second person who picks the same time joins that showing as another attendee.
- No overlapping scheduled showings for one agent (an exclusion constraint
  using `btree_gist`). The ranges are half-open, so 4:00 to 4:30 and 4:30 to
  5:00 can follow each other. Travel time between addresses is added in code.
- One notice per kind, showing and recipient key (a unique key), so a retry or
  a later attendee cannot text the same person twice.

An attendee is booked on the page, by the team, or by the Zillow lead agent
(`source` is `page`, `team` or `lead_agent`). A lead the lead agent books in an
email thread may not have given a mobile number or an email yet, so both can
be empty; such a row is tied to its Zillow thread. A booked mobile number, and
a booked Zillow thread, each appear once per showing (partial unique indexes
that ignore empty values and cancelled rows). Every attendee gets a random
cancel token.

## Notices

Every text, email, calendar event and invite is a row in `showing_notices`
before it is anything else (`channel` is `sms`, `email`, `calendar_event` or
`calendar_invite`). Code that owes a notice calls `outbox.enqueue` inside its own
transaction, so the notice is saved with the booking or not at all. Queuing the
same kind, showing and recipient key again returns the row already saved.

A row starts `pending`, or `held` when it waits for another notice
(`waits_for_notice_id`, see Held notices below), and ends as one of:

| Status | Meaning |
| --- | --- |
| `captured` | Rendered and kept on the row. No provider was called. |
| `sent` | The provider accepted it. `provider_id` and `provider_status` hold its answer. |
| `failed` | The provider refused it, such as a number that has opted out. Nothing went out. |
| `unknown` | It may have gone out: a timeout, a lost answer or a send cut off midway. Never retried. |
| `skipped` | The send mode is off. |
| `cancelled` | Never sent: the booking was withdrawn, or the confirmation text it waited for was not delivered. |

`outbox.deliver` delivers one row at once, and `outbox.drain` delivers every
row due by its `now` argument. A drain settles the rows the guard skips or
captures together, then takes the rows it would send one at a time. Each is
claimed just before its own provider call: the row is locked with
`FOR UPDATE SKIP LOCKED`, the guard is asked again, `attempts` goes up and
`sending_started_at` is set, and that is committed before the provider is
called. Two runs at once therefore never take the same row, a run that stops
partway leaves the rows it had not reached `pending` for the next run, and a
row still marked as sending ten minutes later was cut off midway and becomes
`unknown`. When nothing went out and trying later
may work (Quo out of reach or rate limited), the row stays `pending` with a
later `not_before`, up to five attempts.

Nothing sends email yet. An email notice is captured like any other, and one
the guard would send is saved as `failed` with the reason `no_email_sender`.

`outbox.py` is the only file in this module that hands a row to a provider.
`test_showing_boundaries.py` fails if another file uses a sending method (a
text, or a calendar event write, which invites people), if the Quo sender is
imported anywhere but `outbox.py` or the Google calendar adapter anywhere but
`connections.py`, if a file other than `google_oauth.py` and
`google_calendar.py` names Google's hosts, or if anything here uses the repo's
general `send_message` or `create_calendar_event`, which have no guard.

## Send modes and the guard

The setting is the `showing_bookings_config` row in `app_settings`:

| Field | Default | Meaning |
| --- | --- | --- |
| `send_mode` | `capture` | `off` skips every notice; `capture` keeps them without sending; `live` lets the guard send |
| `live_units` | `[]` | units whose notices really go out in production |
| `test_sends_enabled` | `false` | in live mode, also send notices that reach only test recipients |
| `lead_agent_uses_open_times` | `false` | the Zillow lead agent offers these open times |
| `public_booking_enabled` | unset | the public booking routes answer; unset is off in production, on elsewhere |
| `public_limits` | 2, 2, 6, 50 | upcoming showings per mobile and per email, bookings per IP address a day, page bookings from everyone a day |

`guard.decide` is one pure function. A notice is sent only when the mode is
`live` and either this is production (`RAILWAY_ENVIRONMENT_NAME` is
`production`) and the unit is live, or test sends are on and every address the
notice reaches is a test recipient. A calendar event counts every guest, so an
invite that also names someone else is captured. Everything else is captured.
A preview's database is a copy of production's rows, so outside production
nothing but a test send can go out, whatever the row says.

Test recipients come from two environment variables, read each time:
`SHOWING_TEST_PHONES` and `SHOWING_TEST_EMAILS`, comma separated. Entries that
are not a US mobile number or an email address are ignored.

Each delivered row records the `mode` it was delivered in and the guard's
reason (`guard_reason`), so the Messages list can say why a row was captured.

## Providers

`providers.py` defines what the module needs from outside:

- `TextSender`: send a text and read its status. `quo.QuoTextSender` sends from
  the shared team line through the shared OpenPhone client. Quo's refusals
  (opted out `0201400`, invalid number `0202400`, unreachable `0203400`) and
  other 4xx answers become `NotSent`; a 408, a 5xx or a timeout propagates and
  is saved as `unknown`. A 202 means Quo accepted the text and nothing more: on
  2026-11-15 Quo accepted a send to a well-formed number that does not exist.
  A number like that is not caught when the text is sent; reading the text's
  status afterwards (`outbox.refresh_text_status`) is what catches it.
- `CalendarSource`: busy blocks for a set of calendars over a window (raising
  `CalendarUnreadable`, with the calendars that were read, when one cannot be
  read), and creating, changing and cancelling events with invited guests.
  `google_calendar.GoogleCalendar` is the Google one, described under Calendar
  connections below.

Stand-ins in `fakes.py` replace both in tests and later steps:
`RecordingTextSender` keeps every text in order and can refuse a number, lose
an answer or report a text undelivered; `InMemoryCalendar` holds busy blocks
and events and can lose a calendar. `seed.py` has two made-up agents, which
`api/seed_db.py` saves in non-production databases, and `fill_sample_week`,
which fills an `InMemoryCalendar` with a made-up week of their busy times.

The outbox uses the live senders when none are passed. Under pytest that raises
instead of reaching Quo or Google: `google_http.client`, the one HTTP client
for Google, refuses to run under pytest, and tests install `FakeGoogle`
(`api/src/tests/showing_google.py`) behind `httpx.MockTransport` instead.

## Calendar connections

A connection is one Google account that has granted calendar access, a row in
`calendar_connections` keyed by Google's stable account id (`google_subject`).
On day one the shared team account connects once and reads the calendars the
agents share with it; an agent with a Sernia sign-in may connect their own.

### Connecting

1. A signed-in team member presses Connect on the Booking settings tab.
   `POST /calendar/connect` answers with Google's consent URL for the OAuth
   client in the Google Cloud project `sernia-showings`, which is Internal:
   only serniacapital.com accounts can approve it, with no Google review. It
   asks for offline access to two calendar permissions,
   `calendar.events.freebusy` (free/busy of every calendar the account can
   see) and `calendar.app.created` (calendars the app itself creates), plus
   `openid` and `email` to learn which account approved.
2. Google sends the browser to `/api/showing-bookings/calendar/callback`, the
   module's one route without sign-in (`calendar_callback.py`). The request
   carries no sign-in, so who started the connection travels in `state`:
   the Clerk user id, their Sernia email, the site they started from and a
   nonce, signed with `SESSION_SECRET_KEY` and good for ten minutes. A
   missing, altered or expired state saves nothing.
3. The callback trades Google's one-time code for tokens, checks that both
   calendar permissions were granted, makes sure the account has a calendar
   named "Sernia showings" (created once; a reconnect keeps it), and saves the
   row with the refresh token encrypted. It answers with a small page that
   sends the browser back to the tab with the result, such as
   `?calendar=connected`. A page, not a redirect status, because the web
   server's `/api` proxy follows redirects itself.

The redirect URI is built from an allowed origin, never from a request header:
`https://eesposito.com`, `https://dev.eesposito.com` or `http://localhost` on
any port. Google accepts only redirect URIs registered on the client:

- `https://eesposito.com/api/showing-bookings/calendar/callback`
- `https://dev.eesposito.com/api/showing-bookings/calendar/callback`
- `http://localhost:5173/api/showing-bookings/calendar/callback`, plus a
  worktree's own web port to connect from that worktree

### Using a connection

`connections.calendar_for_connection(row)` is the `CalendarSource` for a
connection; tests replace it with one returning an `InMemoryCalendar`. Reads go
through Google's free/busy query, which leaves out events marked free; a
calendar Google reports an error for, or leaves out, is unreadable and never
counted as free. Event writes (the outbox's alone) go to the showings calendar
with `sendUpdates=all`, so each guest gets the invitation, change or
cancellation by email. The outbox sends a calendar notice through the
connection whose showings calendar the notice names.

Access tokens are refreshed from the stored refresh token as needed and held in
memory until a minute before they expire, never saved. Each read through Google
saves `last_read_at`, `last_read_ok` and `last_error` on the row. When Google
rejects the refresh token (`invalid_grant`: access removed in the Google
account, or expired) the row becomes `lost` and an error-level log goes out,
which the Logfire error alert picks up; its calendars then count as busy until
someone presses Reconnect.

"Check now" reads the account's own free/busy for the next day once.
"Disconnect" revokes the grant at Google, then marks the row `revoked` and
drops its token; the showings calendar stays in the account, and connecting
again reuses it.

### Usable here

A connection is usable here when it is `connected`, this environment has the
Google client, and its token decrypts with this environment's key. Anything
else is unusable here: its calendars read as unreadable (its agents count as
busy), its calendar notices fail, and Check now and Disconnect say why
instead of reaching Google. That is what keeps production's grants out of a
preview: a preview's database is a copy of production's rows, and previews do
not get production's key.

### Settings

Read at call time; values are never logged or returned:

| Variable | What it is |
| --- | --- |
| `GOOGLE_SHOWINGS_OAUTH_CLIENT_ID` | The OAuth client in the `sernia-showings` project. Without it and the secret, the tab says calendar connections are not set up here, and nothing else changes. |
| `GOOGLE_SHOWINGS_OAUTH_CLIENT_SECRET` | That client's secret. |
| `SHOWING_TOKEN_KEY` | The Fernet key that encrypts refresh tokens. Each environment that connects calendars has its own; production's is never set anywhere else, and previews have none. |

Make a key with
`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
A token saved under one key cannot be read under another, so changing the key
means reconnecting each account.

The callback's query holds Google's one-time code and the state, so the app's
own logs and traces leave it out: the web proxy (`server.js`) logs each
request's method and path only, and the API's request tracing skips the
callback (`api/index.py`). The hosting platform's own request log is outside
the app's control.

### The live check

`scripts/showing_calendar_live_check.py` checks a connection against Google by
hand: one free/busy read, then one event on the showings calendar created and
cancelled. Its only guest must be an address in `SHOWING_TEST_EMAILS`; it
refuses any other. Run it from the repository root against the database and
environment where the account was connected:

```bash
uv run python -m scripts.showing_calendar_live_check --account ACCOUNT --guest GUEST
```

## Showing agents

An agent is a row in `showing_agents`, managed on the Booking settings tab
(`agents.py`): name, email, mobile, weekly hours and preferred windows (the
listings' `PreferredWindow` shape, where `end` is the last start), priority
(1 hosts before 2), the calendars that make them busy, and the connection
that reads them. Every save is checked (`agents.agent_problems`): a name,
priority 1 to 99, at most 10 calendar addresses without spaces or repeats, and
windows with days whose first start is not after the last. An email belongs to
one agent.

- No hours means the whole showing window.
- No connection chosen means the team's: the oldest connection that is
  connected. An agent added before any calendar is connected is read once one
  is.
- An agent with no calendars listed counts as busy.
- Deactivating keeps the row, so past showings still name the agent, and gives
  them no new showings. Showings already booked keep their host.

## Open times

`open_times.py`, from the design's "Open times":

1. Start times every 30 minutes inside the showing window, in the showing
   rules' time zone, from the notice period out to 14 days out. The notice is
   the showing rules' `showing_notice_hours` (24 unless changed), or the
   listing's own `showing_notice_hours` when it is longer. Blackout dates are
   skipped, and so is every day from the listing's `off_market_on`. A wall
   time that does not exist on a daylight-saving change is skipped.
2. A start is open when an active agent is within their hours, free on their
   calendars, and not in another scheduled showing, with 30 minutes' travel
   between different addresses and none at the same address. Two showings are
   at the same address when they are at the same unit, in the same building,
   or their addresses read the same, ignoring case and punctuation. A unit key
   is a property and a unit joined by a hyphen (`320-03` is property 320, unit
   03), so two keys with a hyphen and the same part before the last one, such
   as `320-03` and `320-05`, are one building. A key with no hyphen falls back
   to the address.
3. A scheduled showing at the unit that has room is offered as a join, without
   reading the host's calendar again. A full one is not offered, and nothing
   else starts at that unit while a showing is on.
4. Order: joins, then times the unit or an available agent prefers, then the
   rest, each group by time.
5. The host of a new time: an agent already at the same address just before or
   after, then one who prefers the time, then the lower priority number, then
   whoever has fewer scheduled showings that week (Monday to Sunday).

The constants (30 minutes, 4 places, every 30 minutes, 14 days, 30 minutes'
travel, 90 seconds' cache) are in `schema.py`.

What other code calls:

| Call | Answers |
| --- | --- |
| `offers_for_unit(unit, now=)` | the unit's offers in order: start, `join` or `new`, preferred, places left, host, and the showing a join adds to; `why_none` when there are none |
| `recheck(unit, start, now=, session=)` | `HostCheck`: the host and kind, or a refusal code and message (`unknown_unit`, `not_a_start_time`, `outside_window`, `blackout_date`, `off_market`, `inside_notice`, `too_far_ahead`, `full`, `no_agent_free`). Reads calendars with no cached answer. Pass the booking's session to see showings as that transaction does |
| `open_times(now=, units=)` | the same for several units (by default every unit on offer), and how each agent's calendars were read |

Every call takes `now`. Calendars come through
`connections.calendar_for_connection`: one free/busy read per connection
covers its agents, and the answer is kept in memory for 90 seconds per
connection and set of calendars. A calendar that cannot be read makes its
agent count as busy, never free; it is logged at error level and the tab shows
it.

### The sample calendar outside production

Outside production (`RAILWAY_ENVIRONMENT_NAME` is not `production`), an agent
with no usable connection is read from `seed.sample_calendar`, the made-up week
of `fill_sample_week`, so local runs and previews show open times with no
Google account. The answer says so (`sample_calendar_in_use`), and so does the
tab. In production such an agent counts as busy.

A preview's database is a copy of production's, which has no agents yet. On
the Booking settings tab, **Load sample agents** (`POST /agents/load-samples`)
adds the two made-up agents from `seed.py`; it is refused in production and
shown only outside it. With them, every unit on offer in the preview shows
open times from the sample week.

## The lead agent's tour source

`lead_tours.py` gives the Zillow lead agent its tour times, behind the setting
`lead_agent_uses_open_times` (off by default). `zillow_leads/handle.py` asks
`tour_source_for_lead_agent` for each lead: with the switch off, or the setting
unreadable, it gets its own `NoTours` and behaves exactly as before.

With it on, `BookingTourSource` answers `zillow_leads.tours.TourSource`:

- open times: the starts every unit on offer can take (as a new time or a
  join), each with the agent who would host. The case lists times for no
  particular unit, so a time open at one unit and not another is left out
  rather than offered for the wrong unit.
- booked: the showings with room, by unit, which the lead policy offers first.

The source passes on only times that pass the lead agent's own fixed rules
(`case.within_showing_rules`: its 24-hour floor `case.MIN_NOTICE`, the window,
blackout dates), joins included, since `tours_for_case` filters open times by
those rules but not booked tours. A join the booking page offers under a
shorter notice is therefore not given to the lead agent; the page's own offers
are unchanged.

## Public booking

`public_routes.py` is a second router with no sign-in, mounted so every route
is under `/api/showing-bookings/public/`, the only prefix the web server's
`/api` proxy forwards for a Sernia host. Nothing else in the module is public;
`test_showing_calendar_connections.py` checks every other route needs the
Sernia sign-in.

### The switch

`public_booking_enabled` in the settings row is true, false or unset. Unset
means off in production and on everywhere else. While it is off, every public
route answers 404, as for a path that does not exist, and saves nothing,
except two: `POST /bookings/lookup` and `POST /bookings/cancel` answer either
way, so a person booked before the team closed booking can still read and
cancel from the link in their text. A cancel token exists only for a booking
once made, so they show nothing while booking has never been on. The
Send mode section of the tab has the control, "Open booking to the public",
which sets it through `PUT /settings/public-booking` (`null` goes back to the
default); a send-mode save keeps it as stored.

### Routes

Bodies are JSON. A refusal answers
`{"detail": {"code", "message", "fields"}}`, where `fields` maps each form
field to the line shown beside it (`name`, `mobile`, `email`, `start`,
`confirmations.lease_start`, `confirmations.pets`, `confirmations.texts`,
`confirmations`, `turnstile`). Bodies are read from the raw request, not
declared as parameters, so request tracing records no names, numbers or
tokens; a cancel token travels in the body for the same reason.

| Route | Answers |
| --- | --- |
| `GET /units` | the units on offer, for the list at `/showings` (`public_units.py`): each unit's key, address, asking rent, `lease_start_earliest` and `lease_start_latest`, `lease_start` worded by `wording.lease_start_dates` (the rule the form's tick uses), and `has_open_times`; plus the Turnstile site key for the call line. Ordered by address, then unit. One `open_times` call for every unit, so calendars are read once per connection and the 90-second cache serves the booking page that follows. No host names, nothing about who is booked. |
| `GET /call-line` | `{turnstile_site_key}` and nothing else, for the call line on a page with no unit (the Sernia home page). Reads no listings and no calendars, so the page can answer at once; the 404 while the switch is off tells the page to leave the call line out. |
| `GET /units/{unit}` | the listing's unit key, address, rent, showing length, time zone, the confirmations with their wording and version, `confirmations_version`, the consent small print, the privacy and SMS terms paths, the Turnstile site key, and the offers in order (`start`, `join`, `preferred`, `places_left`). No host names. 404 `unknown_unit` for a unit not on offer. |
| `POST /units/{unit}/bookings` | `{start, name, mobile, email, confirmations: {lease_start, pets, texts}, turnstile_token, confirmations_version?, lead_token?}`. 201 with the cancel token, `kind` (`new` or `join`), the confirmation text's status and the booking as below. |
| `POST /bookings/lookup` | `{cancel_token}`: status, unit, address, start and end, time zone, the mobile with all but the last two digits hidden, the corrections left, `started` (the start has passed) and `cancelled_by_team` (cancelled by the team or because the unit went off the market). |
| `POST /bookings/mobile` | `{cancel_token, mobile}`: texts the confirmation to the new number, at most twice per booking; the same answer as the lookup. |
| `POST /bookings/cancel` | `{cancel_token}`: cancels the booking (see Cancelling below) and answers as the lookup does. A booking already cancelled answers the same and sends nothing; 409 `started` once the showing has started; 404 `unknown_booking`. |
| `POST /team-phone` | `{turnstile_token}`: the team number (`SERNIA_TEAM_PHONE`), its display form, the extension (`SERNIA_TEAM_PHONE_EXTENSION`, default 1) and a `tel:` link that dials it. |

### A booking

`bookings.py`, in order:

1. The unit is a listing on offer. The fields are checked together: a name, a
   US mobile (stored in E.164), one email address, a start with a time zone,
   and the confirmations the listing asks for. Everything wrong is answered at
   once with 422 `invalid`. A `confirmations_version` from the page that no
   longer matches (the listing's lease dates changed) is 409.
2. Turnstile (`turnstile.py`): 403 `turnstile_failed`, or 503 in production
   with no secret.
3. One transaction, holding advisory locks: first one lock shared by every
   public booking (`BOOKING_LOCK`), then the unit and start, the mobile and
   the email:
   - the limits (`public_limits` in the settings row): upcoming booked
     showings per mobile (default 2) and per email (2), page bookings from
     everyone in 24 hours (50), and bookings from one IP address in 24 hours
     (6), withdrawn ones counted in the last two. 429 `limit_mobile`,
     `limit_email`, `limit_site` (no field; "Online booking is paused right
     now. Call us to book.", with a warning in the logs) or `limit_ip`. The IP
     address is kept only as a keyed hash.
   - `recheck` with fresh calendars: 409 `time_gone` or `full`.
   - the showing: new, or the one already booked at that unit and start,
     locked; a full one is 409 `full`, and a mobile already in it 409
     `already_booked`.
   - the attendee: the consent record (time, `wording.TEXTS_CONSENT_VERSION`,
     the page), the lease start and pet confirmations with the wording shown,
     its version and the time, the `?t=` lead token, the user agent, and the
     `Signature-Agent` header.
   - the notices: the prospect's confirmation text, due now; a host event
     notice (`host_event` with the first attendee, `host_event_update` for
     each one who joins); and the prospect's own invite (`prospect_invite`,
     with that prospect as its only guest). The host notice and the invite
     are held behind the confirmation text.
4. After the commit the confirmation text is delivered at once. If Quo refuses
   the number (invalid, unreachable or opted out) the booking is withdrawn:
   the attendee is cancelled with `cancel_reason` `number_refused`, a showing
   left with no one is cancelled (`no_attendees`), its notices are cancelled,
   and the answer is 422 `number_refused` on the mobile field. When someone
   is still booked and a host notice for the showing has gone out (sent or
   captured), may have gone out (unknown) or is being sent, the same
   transaction queues a host event update, due now and not held, which
   renders at delivery without the withdrawn person. When every host notice
   is still held or pending, nothing is queued: they render at delivery.

The shared lock means one booking's limits, re-check and insert never
interleave with another's. Two bookings of one unit and start at the same
moment become one showing with two attendees (the second's re-check sees a
join), and two bookings at different buildings at adjacent times never both
get one host inside the travel gap, which the database's no-overlap rule
cannot see. Volume is a few bookings a day, so one lock is enough.

Lock order, in every transaction that takes more than one: advisory locks
first (the shared booking lock, unit and start, mobile, email), then the
attendee, then its notices, then the showing; several attendees and several
showings are locked in id order. A withdrawal and every cancellation take the
shared booking lock too, so the number of people booked in a showing cannot
change underneath one of them. A listing save that carries an off-market date
(the listing routes, the general agent's tool, and `save_listing` itself)
takes the shared booking lock first, before the listing's own lock and before
counting what the date cancels, and holds it through the save and the
cancellation (`cancelling.lock_bookings`). No booking can then commit between
the count a person agreed to and what is cancelled.

A corrected mobile is texted first; only if Quo does not refuse it does it
replace the old one. The final step takes the number's lock again and checks
the per-mobile limit and the showing once more, so two corrections to one
number at once cannot both win; the loser keeps its old number and gets the
same refusal. The held notices (and any stopped because the old number's
text was undelivered) then wait for the new text, and the booking's
`attention_reason` is cleared. If the host's event already went out (sent or
captured), an update is queued behind the new text.

### The host's event

A host event notice is rendered when the outbox delivers it, not when it is
queued (`host_event.py`, called from `outbox._prepare` under a lock
on the showing): the summary and description list the attendees booked then,
with their current mobiles and emails, and the notice creates the showing's
event if it has none yet (saving its id on the showing) or updates it. So a
showing has one event whatever order its notices are released in: an update
released before the first booking's create creates the event, and the create
then updates it. While another host notice for the showing is being sent, a
notice waits a minute. A showing that is cancelled or has no one booked
cancels the notice ("Not sent: the showing has no one booked"), and so does an
earlier create whose outcome is unknown, rather than risk a second event.
What was rendered is saved on the row, so the Messages list and capture mode
show what went out. The prospect's own invite keeps the payload it was queued
with.

A cancellation of the host's event (`host_event_cancel`) or of a prospect's
invite (`prospect_invite_cancel`) is checked the same way when it is delivered
(`host_event.cancel_check`): it waits a minute while the event is being
created, goes ahead when the event exists, or when its create was captured
(so capture mode keeps the cancellation too), and is cancelled otherwise,
with "never created" or, when an earlier create's outcome is unknown, "check
the calendar and remove it by hand". A cancellation never creates an event.

All wording, with versions, is in `wording.py`: the three confirmations, the
consent small print, and the texts and calendar entries a booking sends.
Emilio reviews it before launch.

### Cancelling

`cancelling.py`. The cancel link opens `/showings/cancel/<cancel token>`, which
shows the booking and a Cancel button; opening the link cancels nothing,
because phones and mail scanners open links to preview them.

| Case | Attendee | Showing | Prospect | Host's event |
| --- | --- | --- | --- | --- |
| A prospect cancels, others still booked | cancelled, `prospect` | stays | a text confirming it, with the unit's booking page; invite withdrawn | updated |
| The last prospect cancels | cancelled, `prospect` | cancelled, `prospect` | the same | cancelled |
| The team removes one attendee | cancelled, `team` | cancelled when they were the last | the team's cancellation text, with the units on offer; invite withdrawn | updated, or cancelled when they were the last |
| The team cancels a showing | each cancelled, `team` | cancelled, `team` | the same | cancelled |
| The unit goes off the market | each cancelled, `off_market` | cancelled, `off_market` | the same | cancelled |

- Notices of a cancelled attendee (or of a cancelled showing) that have not
  gone out are stopped (`cancelled`, "Not sent: the booking was cancelled.");
  ones being sent are left to finish. A cancellation never stops the notices
  an earlier cancellation queued.
- An invite or host's event that went out (sent, captured, unknown or being
  sent) gets a cancellation notice; one still held or pending gets nothing.
  When someone is still booked, the host's event gets an update that renders
  at delivery without them.
- Texts go only to an attendee with a mobile who accepted texts and whose
  confirmation text was not reported undelivered or refused
  (`attention_reason`); a number known to be wrong is not texted again, though
  the invite is still withdrawn. A prospect's
  or the team's cancellation text is delivered at once; calendar changes go
  with the job every minute. Off-market cancellations queue everything for the
  job.
- Up to the start. A prospect cancelling after it is refused with 409
  `started`; so is the team (409 on its routes).
- Twice is safe: a second call finds nothing booked, changes nothing and
  queues nothing.
- Residents are not texted yet: `cancelling.queue_resident_cancellation` is
  where the resident's cancellation text goes, and queues nothing today.
- When a listing is saved with an `off_market_on` date
  (`rental_listings.service.save_listing`, from the rental listings screen,
  the MCP service or the general agent's tool), its scheduled showings from
  that day in the showing rules' time zone that have not started are
  cancelled in the same transaction. The hook is inside `save_listing`, so no
  path saves the date and leaves the showings standing.
- A person agrees first. `cancelling.off_market_effect` says what a date
  would cancel now (showings, people, people texted) without changing
  anything. When it is at least one showing, the rental listing routes refuse
  the save with 409 `cancels_showings` and the counts unless the body has
  `confirm_cancellations: true` (the listings screen asks "This cancels 2
  showings and texts 3 people. Save?" and sends it again), and the general
  agent's `rental_listing_update` waits for a person's approval with that
  sentence in its arguments (`off_market_effect`). With nothing booked from
  the date, both save at once. The Sernia MCP service gets the refusal and
  saves nothing; it has no confirm step yet.
- A prospect's confirmation text and invite are checked again each time they
  are delivered, first try or retry (`host_event.FOR_A_BOOKING_KINDS`): once
  the attendee is cancelled or the showing is, they are cancelled and never
  sent. So a send under way when the booking is cancelled, which then fails
  and is retried, never goes out later. The host's event notices already
  render from the showing at delivery.

A place freed by a cancellation is offered again at once, and a cancelled
showing frees its host's time: open times count only scheduled showings and
booked people.

### Held notices

`holds.release_held(now=)` looks at each confirmation text that holds
something:

| The confirmation | The held event and invite |
| --- | --- |
| reported `delivered` | released: pending and due now |
| reported `undelivered` or `failed` | cancelled; the booking gets `attention_reason` `confirmation_undelivered` |
| sent, no final report after 5 minutes | released |
| captured (capture mode has no report) or skipped | released at once |
| refused or otherwise failed, or cancelled | cancelled; the booking is marked for the team |
| pending, or unknown for less than 5 minutes | left held |

The status is read from Quo through `outbox.refresh_text_status`.
`holds.send_pending(now=)` runs `release_held` and then `outbox.drain`. The
APScheduler job `showing-notices` runs it every minute, registered only when
background automation is on; with nothing held or due it returns after one
query. "Send pending now" on the tab runs the same two steps.

### Configuration

Read at call time. Names only; values live in each environment.

| Variable | What it is |
| --- | --- |
| `TURNSTILE_SECRET_KEY` | Turnstile's secret. Unset outside production: Cloudflare's test secret that always passes, which the API answers itself without calling Cloudflare (any non-empty token passes). Unset in production: booking and the team number are refused. |
| `TURNSTILE_SITE_KEY` | The site key the page shows. Unset outside production: Cloudflare's test site key. |
| `SHOWING_PUBLIC_BASE_URL` | Where links in texts point; default `https://serniacapital.com`. The cancel link is `<base>/showings/cancel/<cancel token>`. |
| `SERNIA_TEAM_PHONE_EXTENSION` | The extension for the call line; default 1. The number itself is `SERNIA_TEAM_PHONE`. |

## Team API

Mounted at `/api/showing-bookings`, every route behind the Sernia sign-in:

- `GET /settings`: the settings, the environment, and the test recipients, shortened.
- `PATCH /settings`: `{settings, based_on}`. A save from a stale screen answers
  409. A live unit must be a rental listing and is saved as the listing spells it.
  The lead agent's switch is kept as stored.
- `PUT /settings/lead-agent`: `{lead_agent_uses_open_times}`, changing nothing else.
- `GET /notices`: notices newest first, with their wording, status and mode.
- `POST /notices/send-pending`: run `holds.send_pending` once (release or
  stop held notices, then `drain`) and answer its counts.
- `PUT /settings/public-booking`: `{public_booking_enabled: true | false | null}`,
  changing nothing else.
- `GET /calendar/connections`: each connection with its last read and whether
  it is usable here, and whether this environment can connect calendars.
- `POST /calendar/connect`: `{origin, account?}`, Google's consent URL;
  `account` preselects the account for a reconnect.
- `POST /calendar/connections/{id}/check` and `.../disconnect`.
- `GET /agents`, `POST /agents`, `PUT /agents/{id}`,
  `POST /agents/{id}/deactivate` and `.../activate`. A failed check answers
  422 with `detail.problems`.
- `POST /agents/load-samples`: add the made-up agents. 403 in production.
- `GET /showings`: `open_times` for every unit on offer, the scheduled showings
  in the next 14 days with host, attendee count and the people booked (name,
  mobile, email, whether they take texts, and `attention_reason` when the
  booking needs a call), the lead agent's switch and the send mode.
- `POST /showings/{id}/cancel`: cancel a showing and everyone in it.
- `POST /showings/{id}/attendees/{attendee_id}/remove`: remove one person.
  Both answer `{showing_id, showing_status, attendees_cancelled, texts_queued}`;
  404 for no such showing or person, 409 once the showing has started.

The Booking settings tab (`/systems/leasing-leads/bookings`) shows the Send
mode, Messages, Calendar connections, Showing agents and Showings sections.
The Showings section holds the lead agent's switch, lists who is coming to
each showing with a mark on a booking that needs a call, and has Cancel
showing and Remove, each behind a confirmation that says what will be sent.

## Tests

```bash
./scripts/worktree-test.sh api/src/tests/test_showing_*.py
```

They use the worktree's test database and the stand-ins; nothing reaches Quo
or Google. `test_showing_google_oauth.py` and `test_showing_google_calendar.py`
cover the Google code without a database; `test_showing_calendar_connections.py`
covers connecting, using and disconnecting end to end, including that no
token, code or secret reaches the logs or the browser.
`test_showing_open_times.py` holds the design's worked example and each rule;
`test_showing_agents.py` the agents, Showings and switch routes;
`test_showing_lead_tours.py` the tour source and that the lead agent offers no
times with the switch off or unreadable.
`test_showing_public_booking.py` runs bookings through the public routes with
a moved clock and checks the rows left behind (capture and live mode, held
notices released, stopped or let go after five minutes, a refused number
withdrawn, joins, two bookings at once); `test_showing_public_concurrency.py` covers bookings that race (the shared
lock and the travel gap, two corrections to one number) and the host's event
rendered at delivery; `test_showing_public_routes.py`
covers the switch, each refusal and its field, the limits, the team number,
mobile corrections, what a booking saves, and that no answer shows a host,
another attendee or the team number. `test_showing_public_units.py` covers the
units list: order, wording, no units, no agent free, one calendar read for the
list, the switch, nothing about hosts or bookings, the address in the unit read,
and the call line's route (the site key alone, no listing or calendar read, the switch).
`test_showing_cancelling.py` runs each cancellation through the routes: a
prospect with others booked, the last one, twice, after the start, an unknown
link, while the notices are held, while the event is being created, after the
event and invite went out, the team's two actions, an off-market date saved
through the rental listing routes, the place offered again, and that no answer
shows another attendee.

## Later steps

- A text to the team when a connection is lost; today it is an error-level log.
- The resident's texts (new showing and cancellation), reminders, texts to
  the host, and the team's other actions (add a booking for a caller, change
  the host, mark attendance).
- Reminders, per-lead links, and the lead agent's own showing in its case.
