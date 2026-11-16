# Showing bookings

Booking in-person showings of Sernia Capital's rental units: who can host a
showing, the showings booked, and every text or calendar change they owe. The
work is tracked in [issue #368](https://github.com/EmilioEsposito/portfolio/issues/368)
and lands one pull request at a time, each with sending switched off or in
capture mode.

The first steps hold the tables, the notice outbox, the test harness, the
calendar connections, the showing agents and the open times. Nothing here books a showing yet, and nothing is sent:
the setting starts in capture mode, and outside production only test
recipients can ever be sent to.

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

A row starts `pending` and ends as one of:

| Status | Meaning |
| --- | --- |
| `captured` | Rendered and kept on the row. No provider was called. |
| `sent` | The provider accepted it. `provider_id` and `provider_status` hold its answer. |
| `failed` | The provider refused it, such as a number that has opted out. Nothing went out. |
| `unknown` | It may have gone out: a timeout, a lost answer or a send cut off midway. Never retried. |
| `skipped` | The send mode is off. |

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

## Team API

Mounted at `/api/showing-bookings`, every route behind the Sernia sign-in:

- `GET /settings`: the settings, the environment, and the test recipients, shortened.
- `PATCH /settings`: `{settings, based_on}`. A save from a stale screen answers
  409. A live unit must be a rental listing and is saved as the listing spells it.
  The lead agent's switch is kept as stored.
- `PUT /settings/lead-agent`: `{lead_agent_uses_open_times}`, changing nothing else.
- `GET /notices`: notices newest first, with their wording, status and mode.
- `POST /notices/send-pending`: run `drain` once and answer its counts.
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
  in the next 14 days with host and attendee count, and the lead agent's switch.

The Booking settings tab (`/systems/leasing-leads/bookings`) shows the Send
mode, Messages, Calendar connections, Showing agents and Showings sections.
The Showings section holds the lead agent's switch.

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

## Later steps

- A text to the team when a connection is lost; today it is an error-level log.
- The public booking page, bookings and cancellations, which write the notices
  this outbox delivers, and the scheduled job that drains it every minute.
  They call `recheck` inside the booking's transaction.
- Reminders, per-lead links, and the lead agent's own showing in its case.
