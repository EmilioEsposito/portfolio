# Showing bookings

Booking in-person showings of Sernia Capital's rental units: who can host a
showing, the showings booked, and every text or calendar change they owe. The
work is tracked in [issue #368](https://github.com/EmilioEsposito/portfolio/issues/368)
and lands one pull request at a time, each with sending switched off or in
capture mode.

The design's working copy is a private page, open to the owner only: https://claude.ai/artifact/VQnvyeo2awvhBEc54aNNpE

The steps so far hold the tables, the notice outbox, the test harness, the
calendar connections, the showing agents, the open times, the public
booking API, cancelling, the residents' texts, the team's actions, the
reminders (including the reminder by email in a Zillow lead's thread), the
host's texts, the team's alerts, the host's morning text, the apply text,
joins until 2 hours before, and removing prospects' details after 12 months.
Nothing is sent unless a person switches it on or sends it: the setting starts
in capture mode, outside production only the test list can ever be reached, and
the public booking API is off in production until the team opens it.

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

An attendee is booked on the page, by the team (on the tab, or through the
general agent's showing tools), or by the Zillow lead agent (`source` is
`page`, `team` or `lead_agent`). A lead the lead agent books in an
email thread may not have given a mobile number or an email yet, so both can
be empty; such a row is tied to its Zillow thread, and to the lead conversation
whose reply booked it (`lead_conversation_id`). A booked mobile number, and
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
| `captured` | Rendered and kept on the row. No provider was called. A person can send it by hand (see Captured messages). |
| `sent` | The provider accepted it. `provider_id` and `provider_status` hold its answer. |
| `failed` | The provider refused it, such as a number that has opted out. Nothing went out. |
| `unknown` | It may have gone out: a timeout, a lost answer or a send cut off midway. Never retried. |
| `skipped` | Only rows from when the send mode could be off. |
| `cancelled` | Never sent: the booking was withdrawn, or the confirmation text it waited for was not delivered. |

`outbox.deliver` delivers one row at once, and `outbox.drain` delivers every
row due by its `now` argument. A drain settles the rows the guard
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

The only email ever sent is a lead's reminder in their Zillow thread
(`lead_email_reminder`, see Reminders the evening before). An email notice is
captured like any other; one of another kind that the guard would send is
saved as `failed` with the reason `no_email_sender`. A notice whose check
finds it cannot go and needs the team (the reminder by email with no usable
route) is saved as `failed` before the guard is asked, with no send.

A row a person sent by hand also has `send_approved_by` (their Sernia email)
and `send_approved_at`.

A text notice to several numbers is one group text: the notice's
`recipient` is the first number and `payload.also_to` holds the rest, who
share one thread. The guard counts every number in it, so it is a test send
only when every number is a test recipient.

`outbox.py` is the only file in this module that hands a row to a provider.
`test_showing_boundaries.py` fails if another file uses a sending method (a
text, a group text, or a calendar event write, which invites people), if the
Quo sender is imported anywhere but `outbox.py` or the Google calendar adapter
or the calendar picker's lists (`google_lists.py`, which only reads) anywhere
but `connections.py`, if the Quo contact reader (`quo_contacts.py`,
which sends nothing) is imported anywhere but `residents.py`, if a file other than `google_oauth.py`,
`google_calendar.py` and `google_lists.py` names Google's hosts, or if anything here uses the repo's
general `send_message` or `create_calendar_event`, which have no guard. The
team's alert channel (`push.py`) is held to the same rule: only `outbox.py`
imports it or calls `send_alert`, and the reminders, host's texts and alerts
reach no provider but through the outbox. Email too: only `outbox.py` imports
the Gmail sender (`gmail_thread.py`) or calls `send_in_thread`, and only the
Gmail sender imports the lead agent's module or names one of its sends.

## Send modes and the guard

The setting is the `showing_bookings_config` row in `app_settings`:

| Field | Default | Meaning |
| --- | --- | --- |
| `send_mode` | `capture` | `test` sends only to the test list; `capture` sends nothing by itself; `live` sends to real people, in production, for the units that are live |
| `live_units` | `[]` | the units ticked for live mode |
| `live_all_units` | `false` | every unit is live, including ones added later; the ticks are kept |
| `lead_agent_uses_open_times` | `false` | the Zillow lead agent offers these open times |
| `public_booking_enabled` | unset | the public booking routes answer; unset is off in production, on elsewhere, until it is changed once |
| `public_limits` | 2, 2, 6, 50 | upcoming showings per mobile and per email, bookings per IP address a day, page bookings from everyone a day |

A row saved before test mode needs no migration. It reads as what it meant:
`off` is `capture`; `live` with test sends on and no live units is `test`; any
other row keeps its mode, and the dropped `test_sends_enabled` is ignored, in a
save from a tab left open too. A row that does not read is capture, with no
live unit.

`guard.decide` is one pure function: capture or send. It decides in this order.

1. Outside production (`RAILWAY_ENVIRONMENT_NAME` is not `production`), a notice
   that reaches anyone not on the test list is captured, whatever the row says
   and whoever approved it: a preview's database is a copy of production's rows.
2. A notice a person sent by hand is sent, in any mode.
3. `test` sends when everyone the notice reaches is on the test list.
4. `live` sends in production when the unit is live.
5. Everything else is captured.

Everyone a notice reaches counts: every number of a group text, and every guest
of an event, so an invite that also names someone else is captured. Live mode
has no test sends.

The test list comes from two environment variables, read each time:
`SHOWING_TEST_PHONES` and `SHOWING_TEST_EMAILS`, comma separated. Entries that
are not a US mobile number or an email address are ignored.

Each delivered row records the `mode` it was delivered in and the guard's
reason (`guard_reason`), so the Messages list can say why a row was captured.

### Which units may be booked

In live mode a unit that is not live sends no text to anyone who books it, so
the public and the lead agent may not book it (`settings.unit_open`). In test
and capture mode, and with all units live, every unit on offer is open. The
gate is the setting alone, so a preview follows the row it copied.

| Asks | A unit that is not live |
| --- | --- |
| `GET /public/units` | is left out |
| `GET /public/units/{unit}` and `POST .../bookings`, joins included | 404 `unknown_unit` |
| the lead agent's open times and booked tours | are left out |
| the lead agent's link for the unit | is omitted unless the lead already has a booking there and public booking is enabled |
| a reply that would book it, or move a showing to a new time there | is held, as for a time that is gone |

It never asks for anything that exists: a showing booked there stays, with its
cancel link, its corrections and its messages, which are captured from then on.
The team books and sees every unit. A lead with a place there keeps their link:
the unit's page answers 404 to others, so the web page reads what it shows from
`POST /public/units/{unit}/lead-booking/unit` (`lead_details.unit_for_place`),
and their details are taken as before.

### Captured messages, and sending them by hand

`captured.py`. `GET /notices/captured` lists captured notices, each written as it
would be sent now, to whom, with a state:

| State | Meaning |
| --- | --- |
| `ready` | It can be sent. With a note when it would wait: "Residents get texts only between 8 AM and 9 PM", or, while its confirmation text is ticked or on its way, "Goes once the confirmation is delivered". |
| `too_late` | By the one rule, `delivery_checks.too_late`: the showing has started; for the apply text two days after the start; for the host's morning text noon of its day; for the residents' reminder the start of the last showing it lists. A cancellation counts by the showing's own start. |
| `no_longer_applies` | Anything a delivery would stop it for, with the reason: the booking was cancelled, the showing moved to another host, the number is known to be wrong or was corrected, the event already says this, what it follows was never sent (Told already, below), or its confirmation text did not reach the person. |

The last two stay listed for two days after they were captured. The states come
from the checks a delivery makes, run on a copy of the row (`outbox.preview`), so
the list, the send and the delivery agree.

`POST /notices/send-captured` takes the ids a person picked. In one transaction
each row is locked, must still be captured and ready, and becomes `pending` (or
`held`) with `send_approved_by` and `send_approved_at`. A row already handled is
answered as that. From there the outbox delivers it like any other, with every
check again as it goes (`_prepare`, and `delivery_checks.by_hand_problem`), so
two people pressing at once send nothing twice, and a process that stops midway
leaves rows the job every minute finishes. A request delivers for up to 45
seconds and leaves the rest to the job. It answers what happened to each:
`sent`, `waiting` (until when), `unknown`, `not_sent` (why), `kept` (outside
production, not on the test list) or `handled`.

- A message whose confirmation text is sent with it, or is on its way, waits for
  it by the rules of Held notices. When the text is left unsent, the person
  chose that and the message goes by itself. One captured behind a number since
  corrected waits for the latest text to the number on the booking.
- A confirmation text whose number Quo refuses marks the booking
  (`confirmation_refused`) and alerts the team: nobody is on the page to be told,
  so the booking is not withdrawn.
- Several host event notices for one showing end as one event: each renders from
  the showing as it is, the first creates it, and one that would change nothing
  is not sent. The event goes to the calendar the host's connection writes to
  now, should it have been captured before one was connected.
- The host's morning text lists every showing of that host's day.
- When a person corrects their mobile, what waited for the old text waits for the
  new one and follows the mode again: the approval is cleared, and so it is on
  residents' text brought back by a join.
- Switching modes never sends what is already captured.
- Each send is logged with the person's user id, the notice ids and counts.

### Told already

A captured message counts as told while anything is queued or prepared, so
capture mode keeps a booking's cancellations and changes too. When a message is
about to be really sent that is not enough: `told.missing`, asked by the outbox
only after the guard answered send, cancels it when what it follows was captured
and never sent.

| A message | Goes when |
| --- | --- |
| the residents' cancellation | their new-showing text, or a reminder that listed the showing, went |
| a host's event or prospect's invite cancelled, an old host's event cancelled | there is an event to cancel |
| a host's event update because someone left, with no event yet | something for that host about the showing went, or nothing was captured |
| a host's text that someone left, that the showing is cancelled or that it moved away | the same |

Went means sent, may have gone out or being sent now. Every other message says
something by itself. An update for someone still booked is one: with no event
yet it creates the event, with everyone booked, so a host hears of someone who
joins live a showing that was booked in capture mode.

## Providers

`providers.py` defines what the module needs from outside:

- `TextSender`: send a text, send one group text to 2 to 10 numbers, and read
  a text's status. `quo.QuoTextSender` sends from the shared team line through
  the shared OpenPhone client; a group text is the same call with several
  numbers in `to`. Quo's refusals
  (opted out `0201400`, invalid number `0202400`, unreachable `0203400`) and
  other 4xx answers become `NotSent`; a 408, a 5xx or a timeout propagates and
  is saved as `unknown`. A 202 means Quo accepted the text and nothing more: on
  2026-11-15 Quo accepted a send to a well-formed number that does not exist.
  A number like that is not caught when the text is sent; reading the text's
  status afterwards (`outbox.refresh_text_status`) is what catches it.
- `EmailSender`: an email as a reply in a Zillow lead's thread
  (`ThreadRoute`: the relay address, the thread's subject, the message it
  answers). `gmail_thread.GmailThreadSender` reads the thread's headers and
  sends with `zillow_leads.handle.send_thread_email`, the Gmail call behind
  the lead agent's `send_reply`, from the shared external mailbox. It never
  sends unthreaded: headers it cannot read are `NotSent`, tried again later.
  Gmail's refusals (by the lead agent's own `handle.send_result`) become
  `NotSent`; anything else may have gone out and is saved as `unknown`.
- `TeamAlerter`: an alert on the team's phones. `push.PushTeamAlerter` uses
  the Sernia web push, in production only (see Alerts to the team).
- `CalendarSource`: busy blocks for a set of calendars over a window (raising
  `CalendarUnreadable`, with the calendars that were read, when one cannot be
  read), and creating, changing and cancelling events with invited guests.
  `google_calendar.GoogleCalendar` is the Google one, described under Calendar
  connections below.

Stand-ins in `fakes.py` replace both in tests and later steps:
`RecordingTextSender` keeps every text in order (group texts with all their
numbers) and can refuse a number, lose an answer or report a text undelivered;
`InMemoryCalendar` holds busy blocks and events and can lose a calendar;
`StandInContacts` answers made-up Quo contacts for the resident lookup and can
fail; `RecordingAlerter` keeps every alert the team would have been pushed;
`RecordingEmailSender` keeps every email in a thread and can refuse, lose the
thread or lose an answer. `seed.py` has two made-up agents, which
`api/seed_db.py` saves in non-production databases, and `fill_sample_week`,
which fills an `InMemoryCalendar` with a made-up week of their busy times.

The outbox uses the live senders when none are passed. Under pytest that raises
instead of reaching Quo, Gmail or Google (the Gmail sender raises
`LiveGmailInTests` itself too): `google_http.client`, the one HTTP client
for Google, refuses to run under pytest, and tests install `FakeGoogle`
(`api/src/tests/showing_google.py`) behind `httpx.MockTransport` instead.

## Calendar connections

A connection is one Google account that has granted calendar access, a row in
`calendar_connections` keyed by Google's stable account id (`google_subject`).
On day one the shared team account connects once and reads the calendars the
agents share with it; an agent with a Sernia sign-in may connect their own.

### Connecting

1. A signed-in team member presses Connect on the Booking settings tab
   (`/team/leads/bookings`).
   `POST /calendar/connect` answers with Google's consent URL for the OAuth
   client in the Google Cloud project `sernia-showings`, which is Internal:
   only serniacapital.com accounts can approve it, with no Google review. It
   asks for offline access to the permissions below.
2. Google sends the browser to `/api/showing-bookings/calendar/callback`, the
   module's one route without sign-in (`calendar_callback.py`). The request
   carries no sign-in, so who started the connection travels in `state`:
   the Clerk user id, their Sernia email, the site they started from and a
   nonce, signed with `SESSION_SECRET_KEY` and good for ten minutes. A
   missing, altered or expired state saves nothing.
3. The callback trades Google's one-time code for tokens, checks that both
   required permissions were granted, makes sure the account has a calendar
   named "Sernia showings" (created once; a reconnect keeps it), and saves the
   row with the refresh token encrypted. It answers with a small page that
   sends the browser back to the tab with the result, such as
   `?calendar=connected`. A page, not a redirect status, because the web
   server's `/api` proxy follows redirects itself.

| Permission | Why | Required |
| --- | --- | --- |
| `openid`, `email` | which account approved | yes |
| `calendar.events.freebusy` | free/busy of every calendar the account can see | yes |
| `calendar.app.created` | the showings calendar, which the app creates | yes |
| `calendar.calendarlist.readonly` | the calendar picker: the calendars on the account's own list | no |
| `directory.readonly` | the calendar picker: the people in the Workspace directory, by name and email address | no |

A grant without the last two connects as before and everything but the picker
works with it. A connection made before the picker keeps working as it is; its
card says when a Reconnect would add them (`reconnect_adds_permissions`).

The redirect URI is built from an allowed origin, never from a request header:
`https://serniacapital.com`, `https://dev.eesposito.com` or `http://localhost`
on any port, the sites that serve the team pages (`api/src/utils/team_links.py`).
`https://eesposito.com` is refused. Google accepts only redirect URIs
registered on the client, so each must be listed on it in the Google Cloud
console (project `sernia-showings`, Credentials, the OAuth client):

- `https://serniacapital.com/api/showing-bookings/calendar/callback`
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

### The calendar picker

`calendar_candidates.py` answers which calendars the showing agent form
offers (`GET /calendar/candidates`). It reads Google afresh each time through
the team's connection, as `agents.team_connection` picks it, and only reads:

1. The people in the Workspace directory, by their main calendar (its id is
   the person's primary email address), and the account's own calendar list,
   both to the last page (`google_lists.py`). A calendar in both is in the
   team's group. Left out: the account's own calendar, every connection's
   showings calendar, and Google's virtual calendars (ids ending
   `@group.v.calendar.google.com`). At most 500 are kept, shared ones first,
   and `truncated` says when there were more.
2. A free/busy read of each over the next day, 50 at a time, read exactly as
   availability reads them (`GoogleCalendar.readable`). Only a calendar that
   comes back is offered. The times are dropped and nothing is recorded on the
   connection.

Every calendar saved on the agent in `agent_id` is in the answer whatever
happens: in its group, or as `saved` under its address when it is in neither
list, with `readable` true, false, or null when it could not be checked.

| Answer | When |
| --- | --- |
| `connection` is `none`, `lost` or `unusable` | No account is connected, Google no longer accepts it, or this environment cannot use it (`problem`). Google is not asked. |
| `directory` or `calendar_list` is `not_granted` | The connection's saved permissions lack it, or Google says so. The other list is still read. |
| ... `api_off` | The API is off in the Google Cloud project. |
| ... `sharing_off` | Google says the Workspace does not share its directory with apps. |
| ... `refused` | Google said no, and not why. |
| ... `failed` | Google did not answer, or set a limit. Trying again may work. |
| `check_failed` | Google did not say whether some calendars can be read; those not saved on the agent are left out. |

Google's own wording, names and addresses stay out of the logs, which hold
counts and these words only. Request tracing records the whole URL, query
included, and Google's page tokens can hold an email address, so a page after
the first is not traced.

Setup, once, for the team to be listed:

- The People API must be enabled in the `sernia-showings` project, beside the
  Google Calendar API.
- The Workspace must share its directory with apps: Google Admin console,
  Directory, Directory settings, Sharing settings, External Directory Sharing,
  "Organization data and authenticated user basic profile fields"
  ([Google's note](https://developers.google.com/people/v1/directory)). A
  change can take up to 24 hours.
- A team member presses Reconnect and leaves every box ticked.

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
- An agent with no calendars listed counts as busy, and so does one with a
  calendar that cannot be read.
- The form ticks calendars from the calendar picker (above) and takes no typed
  address. A new agent's own calendar, the one at their email, is ticked while
  it is listed and follows the email until a tick is changed by hand. A ticked
  or saved calendar always stays in the list, so a save drops none that was not
  unticked: one that cannot be read is marked "Can't read: counts as busy".
- The form always saves no connection, so the team's reads every agent. The
  column and the rules above are kept.
- Where no connection is usable, as in a preview, no calendar can be picked;
  Load sample agents adds two that have one.
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
3. A scheduled showing at the unit that has room is offered as a join,
   without reading the host's calendar again, until 2 hours before its start
   (`JOIN_CLOSES_HOURS`), inside the notice period too: the residents were
   told when it was first booked. A notice period shorter than 2 hours applies
   to joins as well. A new time still needs the full notice, and `recheck`
   applies the same rule, so the page and the team's Add a booking offer and
   accept a join 3 hours ahead but refuse one an hour ahead. A full one is not
   offered, and nothing else starts at that unit while a showing is on.
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

With it on, `BookingTourSource` answers `zillow_leads.tours.TourSource`, for the
units that may be booked (see Which units may be booked):

- open times: each unit's own starts (as a new time or a join), each a
  `TourSlot` with its unit, which the case lists under that unit. No host's
  name is passed on.
- booked: the showings with room, by unit, which the lead policy offers first.
  A showing the lead is booked into themselves is left out of both lists for
  their own case (`case.build_case`, from the own showings below), so it is
  never offered back to them; a reply may still state its time.

and `lead_booking_for` adds what belongs to the lead's thread:

- each unit's booking page with the thread's own token
  (`zillow_leads/lead_links.py`), `<SHOWING_PUBLIC_BASE_URL>/showings/<unit>?t=<token>`,
  as the case's booking link. Only for a page that answers: public booking is on,
  and the unit may be booked or the lead has a place there, which the page then
  shows. Other units receive no booking link;
- the thread's own upcoming showings, from the attendee rows tied to it
  (`zillow_thread_id`): unit, start, and booked or the cancel reason. Nothing
  about the host or anyone else booked. A place cancelled because the reply
  that booked it was never sent (`reply_not_sent`) is left out: the lead never
  heard of it.

If the token or those rows cannot be read, the case has no booking links or
own-showings section, and the error is logged.

The source passes on only times that pass the lead agent's own fixed rules
(`case.within_showing_rules`: its 24-hour floor `case.MIN_NOTICE`, the window,
blackout dates), joins included, since `tours_for_case` filters open times by
those rules but not booked tours. A join the booking page offers under a
shorter notice, such as one 3 hours ahead, is therefore not given to the lead
agent; the page's own offers are unchanged.

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
once made, so they show nothing while booking has never been on. The tab's "Who
can book" sets it on or off through `PUT /settings/public-booking`; a send-mode
save keeps it as stored.

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
| `POST /units/{unit}/lead-booking` | `{lead_token}`: the place booked for the token's thread at this unit: `{unit, address, starts_at, ends_at, timezone, details_added, texted}`, nothing else. `details_added` is false for the place the lead agent booked by email while the lead has not added their details, and true when they are in. `texted` is whether their confirmation text went. 404 `no_booking` with neither, or with the lead agent's switch off. See Showings booked by email below. |
| `POST /units/{unit}/lead-booking/unit` | `{lead_token}`: what `GET /units/{unit}` answers, with no offers, for a thread with such a place, whether or not the unit may be booked now. 404 `no_booking` otherwise. |
| `POST /units/{unit}/lead-booking/details` | The booking form's body without `start`, plus `lead_token`: completes that place with the lead's details. 201 with the same answer as a booking, `kind` `details`; 409 `already_completed`; 404 `no_booking`. |
| `POST /bookings/lookup` | `{cancel_token}`: status, unit, address, start and end, time zone, the mobile with all but the last two digits hidden, `texted` (the confirmation text to that mobile went out, or may have; false while it is captured or waiting, when the page says "We'll text" instead of "We texted"), the corrections left, `started` (the start has passed) and `cancelled_by_team` (cancelled by the team or because the unit went off the market). |
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
     `Signature-Agent` header. A lead token that belongs to a Zillow thread
     (`zillow_leads.lead_links.thread_for_token`, the link the lead agent
     sends) also sets `zillow_thread_id`, under a lock on the thread; an
     unknown or malformed token is kept as it came (or dropped, if malformed)
     and ties nothing. A thread already booked into the showing is 409
     `already_booked`, as a mobile already in it is.
   - the notices: the prospect's confirmation text, due now; a host event
     notice (`host_event` with the first attendee, `host_event_update` for
     each one who joins); and the prospect's own invite (`prospect_invite`,
     with that prospect as its only guest). The host notice and the invite
     are held behind the confirmation text. Someone booked without texts, or
     with no mobile, gets no text and nothing is held: the host notice and
     the invite are due now, and with no email there is no invite.

   Steps 3 and 4 are `bookings.place_booking`, which every booking goes
   through: the page, the team (the tab's Add a booking and the general
   agent's `showing_book`, both through `team_actions.book_for_caller`; see
   The team's actions below) and the Zillow lead agent's replies. It takes a `Person` (name, source, and optionally a
   mobile, an email, consent to texts, the form's confirmations, a lead
   token or Zillow thread) and an optional `checks(session)` run under the
   locks, which is how the page applies its limits and a moved showing checks
   that the old place has not changed. It looks up the unit's
   residents itself, before its transaction (`resident_notices.find_for`, which
   may read Quo), so every source tells them the same way: a new showing
   queues their text held behind the confirmation text, or due at once (in the
   daytime) when the booking has none; a join brings back an announcement
   stopped with an earlier failed confirmation, held behind this booking's
   confirmation or due at once without one; a lookup that went wrong marks the
   showing and never stops the booking. A place booked from the lead agent's
   reply stands whatever happens to its confirmation text (the email told the
   lead), so only its invite waits for that text; its host notice and the
   residents' text do not.
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
first (the shared booking lock, unit and start, mobile, email, Zillow thread), then the
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
with their current mobiles and emails and how each booked ("booked on the
booking page", "booked by the team", "booked by email with the lead agent",
and "no texts" for someone who takes none), and the notice creates the showing's
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

### Changing the host

When the team gives a showing to another agent (`team_actions.change_host`),
the old host's event is cancelled and the new host's is created from
scratch, whatever state the host notices are in:

| A host notice of the old host that is | What the move does with it |
| --- | --- |
| `held` | Cancelled ("Not sent: the showing moved to another host.") with its hold cleared, and queued again for the new host, on the new host's calendar, held behind the same confirmation text. |
| `pending`, not started (released, or a booking without texts) | Cancelled the same way, and queued again for the new host, due now. |
| being sent (`pending` with `sending_started_at`) | The move is refused (409 `event_being_sent`): try again in a minute, so the old event's id is always known. |
| `sent` | A `host_event_moved` notice cancels the old event, named by its id, on the old host's showings calendar; the new host gets a `host_event` due now. |
| `captured` | The same `host_event_moved` notice, which capture mode captures in turn; the new host's event is due now. |
| `unknown` | The same notice; at delivery it is stopped with "check the calendar and remove it by hand". The new host's event still goes. |
| `failed` (the old calendar refused it) | Left as it is; the old host has no event. The new host gets a `host_event` due now. |
| `cancelled` and revivable (it waits for a confirmation and was not stopped by a withdrawal, as when the text was reported undelivered) | Its hold is cleared, so nothing for the old host can come back, and a cancelled copy for the new host, on the new host's calendar, takes the hold: a corrected mobile then brings back the new host's event only. |
| `cancelled` otherwise (a withdrawal, an earlier move) | Left as it is; nothing can bring it back. |

A `host_event_moved` notice is one of the cancellation notices
(`cancelling.CANCELLATION_KINDS`): cancelling the showing or its last
attendee afterwards never stops it, so the old host's calendar is always
cleaned up.

The `host_event_moved` notice names the old event (`payload.event_id`), and
the outbox applies it to that event, never to the showing's current one. The
showing's `host_event_id` is cleared, so the new host's event is a create.
Host notices from before the move no longer count for the current host's
event (`host_event.since_last_move`): an unknown old create does not stop the
new host's event, and a later cancellation, update or corrected mobile looks
only at the new host's notices. The moved notice is checked at delivery
(`host_event.moved_check`): it goes ahead when it names an event or the old
create was captured, and is cancelled with "never created", or "check the
calendar and remove it by hand" when the old create's outcome is unknown.
The old host's notices that are stopped lose their hold, so a corrected
mobile does not bring them back. Prospects are told nothing: they were never
shown a host, and their invites stay as they are.

### Cancelling

`cancelling.py`. The cancel link opens `/showings/cancel/<cancel token>`, which
shows the booking and a Cancel button; opening the link cancels nothing,
because phones and mail scanners open links to preview them.

| Case | Attendee | Showing | Prospect | Host's event |
| --- | --- | --- | --- | --- |
| A prospect cancels, others still booked | cancelled, `prospect` | stays | a text confirming it, with the unit's booking page; invite withdrawn | updated |
| The last prospect cancels | cancelled, `prospect` | cancelled, `prospect` | the same | cancelled |
| A lead moved their showing by email: the old place | cancelled, `prospect` | stays, or cancelled (`prospect`) when they were the last | no text; invite withdrawn | updated, or cancelled when they were the last |
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
  the invite is still withdrawn. Nor is a lead about a place whose reply never
  went (`delivery_checks.reply_unsettled`), which they never heard of. A
  prospect's or the team's cancellation text is delivered at once; calendar
  changes go with the job every minute. Off-market cancellations queue
  everything for the job.
- A showing that moved texts the lead no cancellation: the old place is
  cancelled with `moved` (`cancelling.cancel_attendee`), which leaves out that
  one text and nothing else. The invite is withdrawn, and the host's and the
  residents' notices are those of any cancellation. A real cancellation by the
  lead still texts them.
- Up to the start. A prospect cancelling after it is refused with 409
  `started`; so is the team (409 on its routes).
- Twice is safe: a second call finds nothing booked, changes nothing and
  queues nothing.
- A cancelled showing texts its residents when they were told of it (see
  Residents' texts below): the last prospect cancelling, the team cancelling
  or removing the last person, the unit going off the market, and a withdrawn
  booking that leaves the showing empty all call
  `cancelling.queue_resident_cancellation`.
- When a listing is saved with an `off_market_on` date
  (`rental_listings.service.save_listing`, from the rental listings screen,
  the MCP service or the general agent's tool), its scheduled showings from
  that day in the showing rules' time zone that have not started are
  cancelled in the same transaction. The hook is inside `save_listing`, so no
  path saves the date and leaves the showings standing.
- A person agrees first. `cancelling.off_market_effect` says what a date
  would cancel now (showings, people, people texted) without changing
  anything. When it is at least one showing, the rental listing routes refuse
  the save with 409 `cancels_showings` and the counts unless the body carries
  the counts the person agreed to (`confirm_cancellations: {showings, people,
  people_texted}`); they are counted again under the booking lock and must
  match, or the answer is 409 again with the new counts (a bare `true` too).
  The listings screen asks "This cancels 2 showings and texts 3 people.
  Save?", sends the counts back, and asks again if they changed. The general
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
| captured (it has no report) or skipped (an old row) | released at once |
| refused or otherwise failed, or cancelled | cancelled; the booking is marked for the team |
| pending, or unknown for less than 5 minutes | left held |

A confirmation or an invite for a place the lead agent booked also waits,
whatever its time, until the reply that books the place is settled: sent, or
possibly sent (see Showings booked by email). Such a row stays as it is, and
counts as stuck ten minutes after it was due, so the team is alerted.

The status is read from Quo through `outbox.refresh_text_status`.
`holds.send_pending(now=)` runs, in order, `release_held`,
`reminders.queue_due`, `daily_texts.queue_due` (the host's morning text and
the apply text), `outbox.drain` and `alerts.check`. The APScheduler job
`showing-notices` runs it every minute, registered only when background
automation is on; with nothing held, due, recently failed, coming up in the
next two days or owed an apply text it returns after one query. Outside
production, where the job is off, "Run the every-minute job once" on the tab runs
the same steps. Each step runs on its own: one that raises
is logged at error level (`logfire.exception`, naming the step) and the rest
still run, so held notices are always released and due notices always
delivered.

### Residents' texts

Most units are lived in, so a new showing texts the unit's residents
(`residents.py`, `resident_notices.py`).

Who the residents are: the team's Quo contacts whose "Property" and "Unit #"
match the listing's unit key (`320-03` is Property `320`, Unit # `03`), whose
lease ("Lease Start Date" to "Lease End Date") is active on the day of the
showing in the showing rules' time zone, and who are not internal contacts.
The field rules are the Sernia AI tenant tools' own, moved to
`api/src/open_phone/tenants.py` so both read contacts the same way. Each
resident's first phone number is used. No resident on a current lease means
the unit is vacant and nothing is sent.

Where the contacts come from (`residents.contact_source`): in production, the
team's contacts through the shared contact store in `open_phone/service.py`,
which fetches the list at most once every five minutes; everywhere else, made-up
residents on 555-0160 to 555-0199 numbers for every unit
(`fakes.sample_contacts`: none, one or two by the unit key, the same each
time), the way the sample calendar stands in for Google. So no real resident's
number is stored in a preview or a local database. The lookup runs before the
booking transaction, as it may read Quo.

| When | The residents get |
| --- | --- |
| A new showing is booked | one text from the team number (`resident_new_showing`), held behind the booking prospect's confirmation text like the host's event |
| Someone joins a booked showing | nothing |
| The showing is cancelled after they were told | a cancellation text (`resident_cancelled`) to the same numbers |
| The showing is cancelled before their text went out | nothing: their text is stopped with the showing's other notices |

- One notice per showing for all of a unit's residents (recipient key
  `residents`): an ordinary text for one resident, one group text for
  roommates, as the team's tenant texts are, so they share one thread.
- Held like the host's event: released when the confirmation is reported
  delivered (or captured), let go after five minutes with no report, stopped
  when it is undelivered or the booking is withdrawn. When someone else is
  still booked in the showing, the text waits for their confirmation instead
  (`resident_notices.hand_over`), so a showing that goes ahead is still
  announced. When the only people still booked have no confirmation text (the
  team booked them without texts or a mobile), there is nothing to wait for
  and the text is due at once, in the daytime. That is their current confirmation: the latest one sent to the
  mobile on their booking now, so after a corrected mobile the replacement,
  and while a correction is still being sent, the text to the number they
  have (a later correction that takes over re-points it, as for every held
  notice).
- Brought back: when the announcement was stopped because the confirmation it
  waited for failed (undelivered, refused or not sent) or its booking was
  withdrawn, while the showing stays scheduled, a person who joins later
  holds it again behind their own confirmation
  (`resident_notices.bring_back`), and anyone booked who corrects their
  mobile does the same behind their new text, whoever's confirmation the
  announcement last waited on. It is the same row either way, so a showing never gets two
  announcements; one stopped for a cancelled showing or a passed start time
  stays stopped.
- Daytime only: a resident's text due outside 8 AM to 9 PM in the showing
  rules' time zone waits for the next 8 AM (`TextPayload.daytime_timezone`,
  applied by the outbox when the text is due, in capture mode too). One whose
  showing has started by then, or a new-showing text whose showing is
  cancelled, is cancelled instead of sent.
- "Told" means the first text was sent, captured, may have gone out
  (unknown), or is being sent when the showing is cancelled; when the
  cancellation would really go, captured is not enough (Told already, above). The cancellation
  text never overtakes the announcement: while the announcement is being sent
  it waits a minute, then goes only if the announcement went out, and is
  cancelled ("never went out") if it failed or was stopped.
- A lookup that goes wrong never blocks a booking. The booking stands, no
  resident text is queued, and the showing gets `resident_attention`, which
  the Showings section shows beside it: `lookup_failed` (Quo out of reach),
  `lease_dates_missing` (no one at the unit has a current lease, and a
  non-internal contact naming it exactly lacks a readable lease start or end
  date, so it may be lived in; a past or future tenant with both dates
  readable is not this), `unit_key_unreadable` (a key with no hyphen),
  `unit_spelled_differently` (no
  contact matches exactly, but a current tenant's contact names the unit
  another way, such as `3` for `03`), `resident_without_mobile`, or
  `too_many_residents` (more than ten).
- Behind the guard like every notice; every number in the text counts.
- Wording: `wording.resident_new_showing_text` and
  `wording.resident_cancelled_text`, version `RESIDENT_VERSION`.
- No public answer carries anything about residents.

### Showings booked by email

`lead_bookings.py` turns the Zillow lead agent's decisions into bookings,
only while `lead_agent_uses_open_times` is on (the zillow_leads README has the
table of which decision does what, and the order around a send).

- A person sends a reply that changes a booking, unless the lead agent's
  settings let that kind go automatically (a new booking, a move, a
  cancellation, each its own setting, off until saved). The automatic send
  holds any other reply for which `plan_reply` answers a change, and one that
  books or moves without stating the booked time.
- A reply that confirms an in-person time at a listed unit, approved by a
  person or sent automatically,
  calls `place_booking` just before it is sent, with a `Person` of source
  `lead_agent`: the thread's name or "Zillow lead", the thread id, the
  conversation id (`lead_conversation_id`), no email, no consent to texts,
  and the thread's phone number as the mobile if it reads as a US mobile
  (shown to the host, never texted). The same re-check under the shared lock
  applies; a refusal is answered to the caller, which does not send.
- The booking's notices that are due now (the host's event, the residents'
  text) are held for `HOLD` (5 minutes) with `hold_notices_until`, so they
  cannot go before the send's result is known. `after_send` then, when the
  reply is sent, saves `lead_reply_sent_at` on the place and lets the notices
  go in one transaction; it cancels the place with
  reason `reply_not_sent` when Gmail refused it (its notices are stopped
  before they go, so there is nothing to undo), or marks it `reply_uncertain`
  and saves `lead_reply_uncertain_at` when the send's result is unknown.
- A thread holds at most one upcoming place per unit. Confirming another time
  there books the new one first and cancels the old one (reason `prospect`,
  with no text to the lead) after the send; a cancellation in the thread
  cancels with reason `prospect` (`cancelling.cancel_attendee`), with the same
  texts to the lead, the residents and the host as any cancellation. Every
  step is safe to repeat.
- `plan_reply` says what sending a reply would do without changing anything;
  the lead agent puts it on the send card, holds a card whose statement went
  stale, and applies nothing when a person sends edited words.
- A place is in one of three states: never sent (cleaned up), sent
  (`lead_reply_sent_at`: kept, whatever happens to the reply's card
  afterwards) or uncertain (`lead_reply_uncertain_at`: kept and marked; the
  mark itself, `reply_uncertain`, gives way to one about the confirmation
  text). A place this conversation booked and never sent
  (`lead_conversation_id`, not completed through the link, neither sent nor
  uncertain: `delivery_checks.reply_unsettled`) is still the reply's own
  booking: a later approval recovers it, and a definite send failure, a
  rejection or an edited send cancels it. A place booked through the lead's
  link, by an earlier sent reply, or whose own reply was sent, never is.

A moved showing keeps the lead's details (`lead_bookings._moving`). When a
place being moved has them (a mobile, an email and recorded consent to texts)
and its number is not known to be wrong (`attention_reason` is neither
`confirmation_undelivered` nor `confirmation_refused`), the new place takes
over the name, mobile, email, the consent record (accepted, when, version,
page, as given then) and the requirement confirmations. Its source is
`lead_agent`. What says how the old place was completed (the link's token, the
browser) stays with the old one. Otherwise the new place is provisional. With
several old places, the one with the latest consent counts. The old places are
read again under the booking locks; one that changed meanwhile holds the reply
(`place_changed`) to be approved again.

- The confirmation text says it is a move: the new day, time and place, the
  showing it replaces, the cancel link and the STOP line
  (`wording.moved_confirmation_text`, version `MOVED_VERSION`, the same kind
  of notice as any confirmation). The invite waits behind its delivery.
- Nothing reaches the lead before the reply is settled. The text and the invite
  are held like the place's other notices and go when the reply is sent, or
  after the five minutes when its result is uncertain. Beyond that hold they
  never go while the reply is unsettled, and neither does a reminder, an apply
  text or a cancellation text about the place. A process that stopped before
  the send therefore texts the lead nothing until a person decides the card.
  The run every minute leaves such a text and invite out before its limit, so
  they never hold up other messages, and stops them once the showing starts.
- A definite failure cancels the place before anything went, with no text.
- If Quo refuses the number, or the text is undelivered, the place stands,
  marked for the team (`confirmation_refused`, `confirmation_undelivered`), and
  the invite is not sent. The host's event and the residents' text do not wait
  for the text. The reminder the evening before then goes by email.

The lead adds their details (`lead_details.py`). The reply's link carries the
thread's token; on that unit's page the web app's loader asks
`POST /units/{unit}/lead-booking` with the token in the body. When the thread
holds a provisional place there (source `lead_agent`, no consent saved yet,
scheduled and ahead), the page shows that time and the usual form without
the picker. With none but a place that has the details (completed, booked
through the link, or kept from a move whose reply is settled), the page says
"You're booked", with when and where and that the text has a link to cancel.
With neither, it is the normal form.

`POST /units/{unit}/lead-booking/details` checks the fields and ticks as for a
page booking, Turnstile, the page's limits (not counting this place), then under the locks (shared booking lock, mobile, email, thread)
saves the details and the consent record on the same attendee and queues the
confirmation text with the invite held behind it, plus a host event update
held behind it when the host's event already went out. The confirmation is
delivered at once; the held notices are released by the usual rules. If Quo
refuses the number, the details are undone, the held notices stopped, and the
place stays booked (422 `number_refused`, so another number can be tried). A
completed place answers 409 `already_completed`. Neither route returns
anything about the host or anyone else booked, and the first returns no name
and no cancel token.

The Showings section marks such a place "Booked by email with the lead agent"
with "details not added yet" or "details added" (`details_pending`) and a
link to the conversation (`lead_conversation_id`).

Until the lead adds their details, the reminder the evening before goes by
email in their thread (see Reminders the evening before). The Messages list
shows it as "Lead's reminder by email, in their Zillow thread", to the relay
address, with a link to the conversation.

### Reminders the evening before

`reminders.py`. "The evening before" is 6 PM in the showing rules' time zone
on the day before the showing (`reminders.due_at`). `reminders.queue_due(now=)`
is a step of the job every minute: it queues every reminder due by `now` that
is not queued yet, so it is safe to run every minute and catches up after a
missed evening while the showing is still ahead.

| Reminder | Kind | Goes to | Says |
| --- | --- | --- | --- |
| The prospect's | `prospect_reminder`, one per booking | someone still booked, with a mobile, who accepted texts and whose number is not known to be wrong (and not for a place whose lead agent's reply is not settled) | when, where, and the cancel link |
| The residents' | `resident_reminder`, one per unit and day (recipient key `residents:<day>`) | the unit's residents, found afresh that evening by the announcement's own lookup, one group text for roommates | the times of the next day's scheduled showings there, in order |
| A lead's, by email | `lead_email_reminder`, one per booking, channel `email` | a place the Zillow lead agent booked in an email thread whose confirming reply was sent (`lead_reply_sent_at`), for a lead with no mobile we text; only while `lead_agent_uses_open_times` is on | when, where, the cancel link, and the lead's own link to add their details, as a plain-text reply in their Zillow thread |

- A booking made at or after 6 PM the day before gets no reminder: the
  confirmation it just received is enough. A showing first booked then is
  left out of the residents' list too; its own announcement covers it.
- The residents' reminder belongs to the day's first showing and lists every
  showing in `payload.showing_ids`. A cancelled showing never stops it; at
  delivery it is rewritten without the showings cancelled or started since,
  and with none left it is not sent.
- A vacant unit gets none. A lookup problem marks the day's showings at the
  unit (`resident_attention`), as for an announcement. A unit looked up with no
  one to text is not looked up again for an hour in that process.
- Both are checked again when delivered (`delivery_checks.py`): still booked,
  still scheduled, not started, and sent only from 8 AM to 9 PM, so a reminder
  queued after 9 PM goes at 8 AM. The prospect's goes to the mobile on the
  booking then. A cancellation stops a prospect's reminder that has not gone out.
- The step reads the residents before its transaction, as that may reach Quo,
  then queues everything holding the shared booking lock, in the module's lock
  order.
- Wording: `wording.prospect_reminder_text` and `wording.resident_reminder_text`,
  version `REMINDER_VERSION`.

The lead's reminder by email (`lead_email.py`, queued by the same step):

- One reminder per place. Someone owed the reminder text never gets the email,
  and someone whose email still stands (pending, sent, captured, unknown or
  skipped) is not queued the reminder text. If the lead adds a mobile with
  consent before the email goes, the email is cancelled at delivery and the
  text is queued by the next run.
- Not for a place whose reply is uncertain (`reply_uncertain`, no
  `lead_reply_sent_at`) or was never sent, nor one whose reply was sent at or
  after 6 PM the day before (the reply just confirmed it).
- Where it goes: the `reply_route` stored on the lead conversation that booked
  the place (`lead_conversation_id`), never the attendee's own fields, a
  request or anything a lead wrote. `lead_email.route_of` reads it when the
  email is queued and again, under the notice's lock, each time it is about to
  go (`delivery_checks`), and sets the recipient, the subject and the message
  it answers (`EmailPayload.reply_to_message_id`) from it. The conversation
  must be the lead agent's and about the place's own thread, and the address
  a Zillow relay by the lead agent's own rule (`zillow_leads/relay.py`). When
  any of that fails the notice is saved `failed` with the reason (`no_conversation`,
  `conversation_missing`, `not_lead_conversation`, `other_thread`, `no_route`
  or `not_relay`), in every send mode, and the team is alerted; nothing is
  sent. A notice queued while its route was already missing shows a
  placeholder address (`no-route@route-not-found.invalid`) that is never
  sent to.
- At delivery, as well as the reminders' usual checks (still booked, still
  scheduled, not started, 8 AM to 9 PM): cancelled once the lead has a mobile
  we text, or once the lead agent's switch is off.
- Wording: `wording.lead_email_reminder_text`, version `LEAD_EMAIL_VERSION`;
  the subject is the thread's (`Re: …`).

### The host's texts inside 2 hours

`host_texts.py`. The host's calendar event follows every change, but inside 2
hours of the start a change also texts the host, from the team number, at
their own mobile:

| When, less than 2 hours before the start | Kind | Paths |
| --- | --- | --- |
| Someone books, joining or as a new showing | `host_late_join` | the page, the team's booking for a caller (`bookings.place_booking`) |
| Someone cancels, or the team removes them | `host_late_cancel` | the cancel link, the team's Remove |
| The showing is cancelled | `host_showing_cancelled` | the team's Cancel showing, an off-market date |
| The team gives the showing to another host | `host_moved_to` to the new host, `host_moved_from` to the old | the team's Change host |

- Delivered at once after the change commits, except an off-market save, whose
  texts go with the job a minute later. A booking's host text goes after its
  confirmation; a withdrawn booking's is stopped at delivery.
- An agent with no mobile gets none. A text names the person and the showing,
  never a number: no more than the host's event shows.
- At delivery: only before the start; "someone booked" only while that person
  is still booked; the cancel texts to the showing's host then; after a change
  of host, each text to the host it names, the new host's only while they
  still host it and the old host's only while they do not, so after quick
  moves (A to B to A, or A to B to C) every text that goes is true when it goes.
- Wording: `wording.host_joined_text`, `host_left_text`,
  `host_showing_cancelled_text`, `host_moved_to_text`, `host_moved_from_text`,
  version `HOST_TEXT_VERSION`.

### The host's morning text

`daily_texts.py`. From 8 AM in the showing rules' time zone, each agent
hosting at least one scheduled showing that day that has not started gets one
text (`host_morning`) at their own mobile from the team number: the day's
showings in order, each with its time, place and how many people are booked.

- One per agent per day: recipient key `agent:<id>:morning:<day>`, with every
  showing it lists in `payload.showing_ids`.
- Only what the guard lets through: the guard judges a notice by the unit of
  the showing it is attached to, and this text lists several units. So it
  lists only the showings the guard would really send for to the agent's
  number now (`guard.would_send`, which is `decide` answering send),
  and is attached to the first of them. With none (capture mode, or no listed
  unit live) it lists every showing and is captured as usual, so
  the Messages list still shows it. It never mixes the two. Sent by hand, it lists
  every showing of the day. The same rule runs
  when it is queued and again at delivery, where it may move to another of the
  day's showings; the outbox then judges it by that showing's unit. It is the
  only notice that lists more than one unit: the residents' reminder lists one
  unit's showings, and every other notice names one showing.
  The step looks for it by kind and key, so it is queued once whichever
  showing comes first.
- Safe every minute and after a missed run: queued from 8 AM up to noon. A
  morning missed past noon is not sent, and one queued before noon but
  delivered from noon is cancelled ("the morning had passed").
- Written again when it is delivered (`delivery_checks`): the agent's
  scheduled showings that day not started yet, with the people booked then,
  to the agent's mobile then. A cancelled showing never stops it (it is in
  `cancelling.NOT_STOPPED_KINDS`); it drops out of the list, and with nothing
  left the text is not sent.
- An agent with no mobile gets none. It names no prospect and carries no
  prospect's phone number or email.
- Never longer than a text can be (1,600 characters), when queued and when
  written again at delivery: a list too long is compacted (the address once
  in the first line when every showing shares it, then time, unit and count
  per line), and if still too long lists as many showings as fit and ends
  with "…and 3 more showings today."
- Wording: `wording.host_morning_text`, version `HOST_MORNING_VERSION`.

### The apply text

`daily_texts.py`. Two hours after a showing's start, each person still booked
in a showing that was not cancelled, with a mobile and consent to texts, whose
number is not known to be wrong and who is not marked a no-show, gets one text
(`prospect_apply`, recipient key `attendee:<id>`) with the apply link,
`<SHOWING_PUBLIC_BASE_URL>/apply`.

- One per person, never repeated. Someone marked `attended` or left `unknown`
  gets it.
- Daytime only: one due outside 8 AM to 9 PM waits for the next 8 AM.
- Checked again when delivered: a person cancelled, a showing cancelled, a
  no-show marked after it was queued, or a number that stopped being texted
  cancels it.
- Showings that started more than two days ago (`APPLY_LOOKBACK`) owe none,
  so a job that was off for days does not text people about a showing long
  past.
- Wording: `wording.apply_text`, version `APPLY_VERSION`.

### Removing prospects' details after 12 months

`retention.py`. The design keeps a prospect's details for 12 months after the
showing unless they apply. Applications live in another system, which keeps an
applicant's details itself, so here everyone's are removed.

For each showing that started 12 months ago or more and still holds details:

| Row | Changed | Kept |
| --- | --- | --- |
| `showing_attendees` | `name` becomes "Removed"; `mobile`, `email`, `user_agent`, `signature_agent`, `client_ip_hash`, `lead_token` and `texts_consent_page` emptied; `requirement_confirmations` emptied; `cancel_token` replaced by a new random value (`removed-<uuid>`) that was never sent, so the old link answers 404 `unknown_booking` | the row and its id, status, attendance, source, `texts_accepted`, consent time and version, cancel reason, `attention_reason`, Zillow thread and conversation ids, calendar event id and times |
| `showing_notices` of the showing, to anyone (prospects, residents, the host, a lead's reminder by email) | `recipient` becomes `removed` (for the email reminder, the Zillow relay address); `payload` becomes `{"body" or "summary": "Removed 12 months after the showing.", "removed": true}` (for the email reminder, its subject, conversation id and message id go too); `last_error`, when set, becomes that same marker, since a provider's message can quote a number or an address | the row and its kind, channel, status, mode, guard reason, attempts, provider id and status (codes and opaque ids), who sent it by hand (a Sernia email, the team's own) and times |
| `showings` | nothing | everything |

- 12 months is `interval '12 months'` before the database's own clock
  (`now()`). A test may pass an earlier `now`, never a later one: the cut-off
  is 12 months before whichever is earlier.
- In batches of 100 showings, one transaction each, up to 50 batches a run;
  the next run carries on. A showing is taken while any attendee or notice of
  it still holds one of the fields above, so a row cleared before a field was
  covered is taken again; a cleared showing is not, so running it twice
  changes nothing.
- The free-form fields on these rows: a notice's `last_error` (the one that
  can quote a provider's message), the attendee fields above, and payloads.
  Everything else is a code (`cancel_reason`, `attention_reason`,
  `resident_attention`, `guard_reason`, `alert_reason`, `provider_status`), an
  opaque id, or the unit's own address.
- No route calls it. The APScheduler job `showing-details-removal` runs it
  daily at 3:30 AM Eastern, registered only when background automation is on.

### Alerts to the team

`alerts.py`. A notice needs the team when it:

- failed: refused (`failed`) or may have gone out (`unknown`), and either way
  will not be tried again. Not a prospect's confirmation whose number Quo
  refused: the person was told on the page, and the booking is withdrawn or
  marked on the Showings section. One sent by hand counts;
- is stuck: due ten minutes ago and neither gone out nor captured, skipped or
  cancelled; or still held fifteen minutes after its confirmation text went
  (ten minutes past the five-minute release).

A notice merely captured never is. `alerts.check(now=)` is the
job's last step, after the drain. For each notice it saves `alerted_at` and
`alert_reason` on the row and commits first, then sends one alert: so a notice
is alerted about once, even when the alert itself fails. Only notices from the
last two days count. The alert names the kind of notice (`wording.notice_label`),
the unit and the showing's time, never a phone number (`wording.alert_title`,
`wording.alert_body`, version `ALERT_VERSION`), and opens the Booking settings tab.

The channel is the Sernia web push the approval flow already uses
(`push.PushTeamAlerter`, through `notify_all_sernia_users`): every phone and
browser on which a team member turned on Sernia notifications, with no new
provider or secret. `outbox.send_team_alert` is the only way to it, and it
pushes only in production; elsewhere the alert is logged and kept on the row,
never pushed. Tests pass `fakes.RecordingAlerter`; the live one refuses to run
under pytest.

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

- `GET /settings`: the settings, the environment, the test list, shortened, and
  every rental listing (unit and address), for the units that can be live.
- `PATCH /settings`: `{settings, based_on}`. A save from a stale screen answers
  409. A newly ticked unit must be a rental listing and is saved as the listing
  spells it; one ticked already whose listing is gone stays until it is unticked.
  The lead agent's and the public booking switches are kept as stored.
- `PUT /settings/lead-agent`: `{lead_agent_uses_open_times}`, changing nothing else.
- `GET /notices`: notices newest first, with their wording, status and mode,
  what each is in words (`kind_label`), whether it failed or is stuck
  (`attention`), when the team was alerted (`alerted_at`) and who sent it by hand
  and when (`send_approved_by`, `send_approved_at`); a group text's
  other numbers are in `payload.also_to`.
- `GET /notices/captured` and `POST /notices/send-captured` `{ids}`, 1 to 200 of
  them: see Captured messages, and sending them by hand.
- `POST /notices/send-pending`: run `holds.send_pending` once (release or
  stop held notices, queue the reminders due, `drain`, alert the team) and
  answer its counts. 403 in production, where the job does this every minute.
- `PUT /settings/public-booking`: `{public_booking_enabled: true | false}`,
  changing nothing else.
- `GET /calendar/connections`: each connection with its last read and whether
  it is usable here, and whether this environment can connect calendars.
- `POST /calendar/connect`: `{origin, account?}`, Google's consent URL;
  `account` preselects the account for a reconnect.
- `POST /calendar/connections/{id}/check` and `.../disconnect`.
- `GET /calendar/candidates?agent_id=`: the calendars the agent form offers,
  read from Google afresh, with every calendar saved on that agent
  (`{connection, account, problem, directory, calendar_list, check_failed,
  truncated, calendars: [{calendar_id, name, group, readable}]}`, see The
  calendar picker). `agent_id` is left out for a new agent; 404 for no such
  agent. No email address is ever in the URL.
- `GET /agents`, `POST /agents`, `PUT /agents/{id}`,
  `POST /agents/{id}/deactivate` and `.../activate`. A failed check answers
  422 with `detail.problems`.
- `POST /agents/load-samples`: add the made-up agents. 403 in production.
- `GET /showings`: `open_times` for every unit on offer, the scheduled showings
  in the next 14 days with host, attendee count and the people booked (name,
  mobile, email, whether they take texts, and `attention_reason` when the
  booking needs a call) and `resident_attention` when the residents were not
  texted.
- `POST /showings/{id}/cancel`: cancel a showing and everyone in it.
- `POST /showings/{id}/attendees/{attendee_id}/remove`: remove one person.
  Both answer `{showing_id, showing_status, attendees_cancelled, texts_queued}`;
  404 for no such showing or person, 409 once the showing has started.
- `POST /showings/bookings`, `POST /showings/{id}/host` and
  `PUT /showings/{id}/attendees/{attendee_id}/attendance`: the team's actions
  below. A refusal answers `{"detail": {"code", "message", "fields"}}`.

`GET /showings` lists showings from 3 days before now (so who came can be
marked) to 14 days ahead, each with its host's id and each person's
`attendance`.

### The team's actions

`team_actions.py`. Each answers what it did and the notices it queued
(`notices`: id, kind, channel, status and whether it is held).

| Route | Body | Does |
| --- | --- | --- |
| `POST /showings/bookings` | `{unit, start, name, mobile?, email?, texts}` | Books someone who called through `place_booking` with source `team`. 201 with `{showing_id, attendee_id, kind, texts_accepted, confirmation, notices}`. |
| `POST /showings/{id}/host` | `{agent_id}` | Gives the showing to another agent (see Changing the host). `{showing_id, host_id, host_name, previous_host_name, notices}`. |
| `PUT /showings/{id}/attendees/{attendee_id}/attendance` | `{attendance: "attended" \| "no_show" \| "unknown"}` | Marks whether the person came; `unknown` clears it. Sends nothing. |

A booking for a caller:

- The team picks one of the unit's current offers (joins included), so the
  same re-check applies: 409 `time_gone` or `full`, 404 `unknown_unit`,
  409 `already_booked` for a mobile already in the showing. Fields are 422
  `invalid` with a line per field: a name is required, a mobile and an email
  are optional and checked when given.
- It skips what only protects the public form: Turnstile, the limits per
  mobile, email, IP address and site, and the confirmation ticks. Team
  bookings do not count towards the site's daily cap.
- `texts: true` with a mobile: the consent record is saved (the time, the
  wording version, and `team:<who took it>` as the page), the confirmation
  text is delivered at once, and the host's event and the invite wait behind
  it, as for a page booking. A number Quo refuses withdraws the booking as on
  the page: 422 `number_refused`.
- Otherwise the attendee is saved as not accepting texts, nothing is texted
  or held, and a later cancellation texts them nothing either.
- The unit's residents are told as for a page booking (see `place_booking`
  above): held behind the confirmation text, or due at once in the daytime
  when there is none. The answer's `notices` lists the residents' text with
  the others. Changing the host and marking attendance tell the residents
  nothing.

The general agent (Sernia AI) is one more caller of the same code, through
its deferred `showing-bookings` capability (`sernia_ai/tools/showing_tools.py`,
documented in [its tool policies](../sernia_ai/tools/README.md#showings-showing_toolspy)):

- `showings_read` reads what the Showings section shows, with each unit's
  booking link or why it has none, and a Zillow thread's own link and
  showings (`lead_tours.lead_booking_for`).
- `showing_book` calls `book_for_caller` with `taken_by` set to the signed-in
  person's email and "(via Sernia AI)", and the approved paragraph as
  `expected_effect`. It may pass `zillow_thread_id`, which
  `book_for_caller` takes as a keyword (the tab's form has no such field) and
  puts on the `Person`: the row is tied to the lead's thread as a page booking
  through the lead's link is, with source `team` and no lead conversation.
- `showing_cancel` calls `cancelling.cancel_showing` or
  `cancelling.remove_attendee`.

The read's document is built in `team_view.py` (`read(now=, unit=,
zillow_thread_id=)`), so the Sernia MCP service's read below is the same
document. It raises `Refused`: 503 `unavailable` when the rental listing data
fails its checks, 404 `unknown_unit`.

Each booking and cancellation waits for a person's approval, on a card that
states what it does and who is told. The agent has no move, host change or
attendance tool.

That statement comes from `effects.py`, so every place that asks a person to
approve a team action from a chat says the same thing. `preview_booking(body,
now=, zillow_thread_id=)` returns a `BookingPreview` (the paragraph, unit,
start, name, time zone). `preview_cancellation(showing_id, attendee_id, now=)`
returns the paragraph. Neither changes anything. Each runs the checks the real
action makes before it saves, and raises `Refused` with a plain message:
`invalid`, `unknown_unit`, `unknown_thread`, `not_open` (a time the unit cannot
be booked for now, with the reason), `unknown_showing`, `unknown_attendee`,
`already_cancelled` or `started`. The paragraph names who is booked or
cancelled, the unit, the time in local words and the host. It says who is
told: the host's calendar event (and a text inside 2 hours), the residents'
text, the prospect's confirmation text and invite, or cancellation texts. It
ends with what the send mode does with those messages (`sending_sentence`).
The booking preview reads calendars afresh (`open_times.recheck`), so the
host it names is the host the booking would get. Because a preview is read
before the action's locks, the actions take the approved paragraph as
`expected_effect`: `book_for_caller` works the preview out again as
`place_booking`'s `checks`, under the booking's locks, and `cancel_showing` and
`remove_attendee` right after the shared booking lock. Unless it reads the same,
they raise `effects.EffectChanged` (a `Refused`, 409 `effect_changed`) before
anything is written or sent. Its `effect` holds the paragraph as it now reads,
for the person to approve again, or is None when the action can no longer be
done (the message says why). A join or calendar change between the approval
and the action therefore changes nothing until a person approves again.

### The Sernia MCP service

`mcp_routes.py`, mounted at `/api/showing-bookings/mcp`, is what the Sernia
MCP service (`apps/sernia_mcp`) calls for its `showings_read`, `showing_book`
and `showing_cancel` tools, which match the general agent's. The MCP service
has no database; it calls here with the signed-in person's Clerk OAuth token.

Only that token is accepted (`mcp_user`): an OAuth access token issued to the
client named by `SERNIA_MCP_CLERK_OAUTH_CLIENT_ID`, for a verified
@serniacapital.com user, as on the rental listing routes. A browser session is
refused here, and the MCP's token is refused on every Team API route above,
so these five routes are all the MCP can reach in this module:

| Route | Body | Does |
| --- | --- | --- |
| `GET /showings` | `?unit=&zillow_thread_id=` | `team_view.read`: the general agent's read document. |
| `POST /showings/bookings/preview` | `{unit, start, name, mobile?, email?, texts_accepted?, zillow_thread_id?}` | `{effect}`: `preview_booking`'s paragraph. Books nothing. |
| `POST /showings/bookings` | the same, plus `effect` | 201 `{result, booking}`. `book_for_caller` with `taken_by` "<email> (via Sernia MCP)". |
| `POST /showings/cancellations/preview` | `{showing_id, attendee_id?}` | `{effect}`: `preview_cancellation`'s paragraph. Cancels nothing. |
| `POST /showings/cancellations` | the same, plus `effect` | `{result, cancellation}`. `cancel_showing`, or `remove_attendee` with an attendee. |

The MCP shows the preview's paragraph on its Approve card and sends it back
on Approve. The booking and cancellation routes work the paragraph out again
first; if it differs, nothing is done and the answer is 409 `effect_changed`
with the paragraph as it now reads in `detail.effect`. They then pass it as
`expected_effect`, so the action checks it once more under its own locks and
refuses the same way (`EffectChanged`; `detail.effect` is null when the action
can no longer be done, and the message says why). Refusals answer
`{"detail": {"code", "message", "fields"}}` with the codes above. These
routes do not check that a person clicked Approve; the card is the MCP's.
There is no route for another host or attendance.

Changing the host refuses: 404 `unknown_showing` or `unknown_agent`; 409
`not_scheduled`, `started`, `same_host`, `event_being_sent`, and the agent's
problem by the open-times rules (`open_times.host_problem`, with calendars
read afresh): `inactive`, `outside_hours`, `calendar_unreadable`,
`calendar_busy`, or `in_another_showing` (overlapping, or without 30 minutes'
travel between buildings; none is needed inside one). The tab lists every
active agent but the host and shows the reason when one is refused.

Attendance refuses 404 `unknown_attendee`, 409 `not_booked` for a cancelled
booking and 409 `not_started` before the start.

The Booking settings tab (`/team/leads/bookings`) starts with a line
saying what happens right now, from the saved settings (and what it will be,
while there are unsaved edits). Then:

- Who can book: the team (always), the public booking switch and the lead
  agent's switch, each saved at once.
- What a booking sends: the three modes with their own settings, saved together.
- Captured messages, with the ones that can be sent ticked and a question, naming
  how many go to real people, before they are.
- Messages (the history), Calendar connections, Showing agents and Showings.

The Showings section lists who is coming to
each showing with a mark on a booking that needs a call and a line when the
residents were not texted, and has Cancel showing and Remove, each behind a
confirmation that says what will be sent. Add a booking opens a short form
(unit, one of its open times, name, and an optional mobile, email and "They
agreed to texts"); it asks first only when something will reach the caller.
Before a showing starts it has Change host; after, each person has Came and
Did not come.

## Tests

```bash
./scripts/worktree-test.sh api/src/tests/test_showing_*.py
```

They use the worktree's test database and the stand-ins; nothing reaches Quo
or Google. `test_showing_google_oauth.py` and `test_showing_google_calendar.py`
cover the Google code without a database; `test_showing_calendar_connections.py`
covers connecting, using and disconnecting end to end, including that no
token, code or secret reaches the logs or the browser.
`test_showing_google_lists.py` covers the picker's reads without a database
(pages, limits, each refusal, 50 calendars at a time), and
`test_showing_calendar_candidates.py` the route: what is offered and left out,
the calendars saved on an agent in every state, a connection without the
picker's permissions, each kind of Google's trouble, and what reaches the logs.
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
`test_showing_team_actions.py` runs the team's actions through the routes:
bookings with and without texts, without a mobile, with a name alone, a join,
a refused number, a time gone, a full showing, the limits not applied; a
change of host after the event went out, while it is held, with a join still
held, in capture mode, after an unknown create, to a busy agent, outside
their hours, across buildings and inside one, while the event is being sent
and after the start; attendance; and every route refused without a sign-in.
`test_showing_cancelling.py` runs each cancellation through the routes: a
prospect with others booked, the last one, twice, after the start, an unknown
link, while the notices are held, while the event is being created, after the
event and invite went out, the team's two actions, an off-market date saved
through the rental listing routes, the place offered again, and that no answer
shows another attendee. `test_showing_residents.py` covers the residents'
texts: held, released, stopped and handed over; joins; the 8 AM to 9 PM window
at both edges and across midnight; vacant units, roommates, leases by the
showing day, internal contacts; each failed lookup and its mark; every
cancellation path with the first text sent, held, stopped or being sent;
capture mode and a live group text with a roommate outside the test list; and
that no public answer mentions residents. `test_showing_reminders.py` covers
the reminders (at 5:59 and 6:00 PM, after a skipped evening and a missed one,
a cancelled, withdrawn or late booking, a caller without texts, a wrong
number; the residents' list with one and three showings, a vacant unit, a
lookup problem, a showing cancelled between queuing and sending, a showing
booked after; never twice), the host's texts inside and outside 2 hours for
each path and a host without a mobile, each alert rule once only, the
channel in and outside production, capture and live mode, the Messages
list, and that no public answer changes. `test_zillow_lead_bookings.py` covers
showings booked by email: nothing with the switch off; booking on approval and
a reply that would change a booking never sent automatically, and one that changes nothing sent as before; nothing to the lead; what is not booked (virtual,
no start, an unlisted unit, a rejected reply); a refused time holding the
reply; a refused, an uncertain and an interrupted send; approving twice;
moves, cancellations and the decisions that make them; and the lead adding
their details (the confirmation and the invite, a refused number, twice,
another thread's or unit's token, the limits, the switches, nothing about
anyone else), and the Showings section. `test_zillow_lead_email_reminder.py`
covers the reminder by email: queued at 6 PM for a place whose reply was sent
and not for one booked after, never sent or uncertain; the text instead for a
lead who added details, before or after it was queued, and never both; none
after a cancellation or with the switch off; the 8 AM to 9 PM window; the
route read from the conversation at delivery, and each missing or unsafe
route failing for the team with nothing sent; capture, live outside
production, a live send to a test recipient once across repeated runs, Gmail's
answers, the live sender refusing under pytest and its own logic with Gmail
mocked; and the Messages list.

`test_zillow_lead_moves.py` covers a showing moved by email that keeps the
lead's details: what the new place takes over; the moved text and the invite
waiting for the send, past the hold too; a refused, an uncertain and an
interrupted send; a refused or undelivered number; no cancellation text for the
old place and one for a real cancellation; when the details are not kept; the
old place changing meanwhile; the link's "You're booked" state and what it
never shows; and the lead's own showing left out of what they are offered.

`test_showing_daily_texts.py` covers the host's morning text (at 7:59 and
8:00, after a missed morning and past noon, one and three showings, an agent
hosting none, an agent without a mobile, a showing cancelled before and after
it is queued, never twice) and the apply text (at two hours, held to the
daytime, a no-show marked before and after it is queued, a cancelled person or
showing, no consent, no mobile, a wrong number, a showing long past, never
twice), and the Messages list's labels. `test_showing_retention.py` covers
removing details (not at 12 months less a day, every field at 12 months,
counts and history kept, a showing 11 months old untouched, twice, the cancel
link answering not found, a later clock unable to remove a younger showing,
the daily job on the database's clock). `test_showing_late_joins.py` covers
joins until 2 hours before (offered 3 hours ahead and not 1, booking,
the team, a new time still refused, the re-check, a full showing, the lead
agent's times unchanged).
`test_showing_guard.py` runs the guard over every mode, environment, tick, set of
addresses and approval, and the rows saved before test mode.
`test_showing_captured.py` covers captured messages: each state and the list, a
send in each mode and environment, twice and at once, a stopped process, the
confirmation text (sent with it, on its way, left unsent, undelivered, refused,
corrected), the daytime, a showing cancelled or moved meanwhile, one event from
several notices, mode switches, alerts, the logs and removing details.
`test_showing_told.py` covers what follows a message that was only captured, in
live and capture mode. `test_showing_unit_gate.py` covers which units may be
booked on every public and lead path, and what stays untouched.
`test_showing_job_steps.py` makes each step of the every-minute job raise in
turn and checks the release and the drain still deliver and the failure is
logged with the step's name.

## Later steps

- A text to the team when a connection is lost; today it is an error-level log.
- The lead policy's sentence for a moved showing that keeps the lead's details
  (no details line then), with an eval run.
