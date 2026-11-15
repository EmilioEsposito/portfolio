# Showing bookings

Booking in-person showings of Sernia Capital's rental units: who can host a
showing, the showings booked, and every text or calendar change they owe. The
work is tracked in [issue #368](https://github.com/EmilioEsposito/portfolio/issues/368)
and lands one pull request at a time, each with sending switched off or in
capture mode.

This first step holds the tables, the notice outbox and the test harness.
Nothing here books a showing yet, and nothing is sent: the setting starts in
capture mode, and outside production only test recipients can ever be sent to.

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

An attendee's mobile number is booked once per showing, and every attendee
gets a random cancel token.

## Notices

Every text, calendar event and invite is a row in `showing_notices` before it
is anything else. Code that owes a notice calls `outbox.enqueue` inside its own
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
row due by its `now` argument. A send is claimed first: the row is locked with
`FOR UPDATE SKIP LOCKED`, `attempts` goes up and `sending_started_at` is set,
and that is committed before the provider is called. Two runs at once therefore
take different rows, and a row still marked as sending ten minutes later was
cut off midway and becomes `unknown`. When nothing went out and trying later
may work (Quo out of reach or rate limited), the row stays `pending` with a
later `not_before`, up to five attempts.

`outbox.py` is the only file in this module that hands a row to a provider.
`test_showing_boundaries.py` fails if another file imports a sender or calls a
sending method, or if anything here uses the repo's general `send_message` or
`create_calendar_event`, which have no guard.

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

Stand-ins in `fakes.py` replace both in tests and later steps:
`RecordingTextSender` keeps every text in order and can refuse a number, lose
an answer or report a text undelivered; `InMemoryCalendar` holds busy blocks
and events and can lose a calendar. `seed.py` has two made-up agents, which
`api/seed_db.py` saves in non-production databases, and `fill_sample_week`,
which fills an `InMemoryCalendar` with a made-up week of their busy times.

The outbox uses the live senders when none are passed. Under pytest that raises
instead of reaching Quo.

## Team API

Mounted at `/api/showing-bookings`, every route behind the Sernia sign-in:

- `GET /settings`: the settings, the environment, and the test recipients, shortened.
- `PATCH /settings`: `{settings, based_on}`. A save from a stale screen answers
  409. A live unit must be a rental listing and is saved as the listing spells it.
- `GET /notices`: notices newest first, with their wording, status and mode.
- `POST /notices/send-pending`: run `drain` once and answer its counts.

The Booking settings tab (`/systems/leasing-leads/bookings`) shows the Messages
and Send mode sections; its other sections are placeholders.

## Tests

```bash
./scripts/worktree-test.sh api/src/tests/test_showing_*.py
```

They use the worktree's test database and the stand-ins; nothing reaches Quo
or Google.

## Later steps

- Calendar connections over OAuth with a Google client of their own, the Google
  `CalendarSource`, and an alert when a connection is lost.
- Open times from agents' hours, calendars and existing showings.
- The public booking page, bookings and cancellations, which write the notices
  this outbox delivers, and the scheduled job that drains it every minute.
- Reminders, and the lead agent offering real times and per-lead links.
