# Rental listings

For each rental unit, the listing: the next lease on offer, what a prospect
may be told about the unit, and our internal notes for deciding. Plus the
showing rules that apply to every unit, and the history of changes to all of
it. The Zillow lead agent's case is built from this
([`sernia_ai/zillow_leads`](../sernia_ai/zillow_leads/README.md)), and the
showing booking system (issue #368) will read it too.

This is not a record of the units themselves or of who lives in them. The
current lease and the current rent stay in the property management system.

```
rental_listings/
├── schema.py    the shapes code depends on: RentalListing, ShowingRules, PreferredWindow
├── checks.py    what must hold before anything is saved or used
├── models.py    the tables
└── service.py   the only code that reads and writes them
```

## Tables

| Table | One row is |
|---|---|
| `rental_listings` | One unit's listing as it stands now. A unit has one listing and keeps it from season to season: when the unit goes back on offer, the asking rent and dates change and the facts carry over |
| `rental_listing_changes` | One saved change, never updated: who made it, when, from where, and the whole listing (or the showing rules) as it read afterwards |

The showing rules (showing window, blackout dates, shared booking link, time
zone) are one row in `app_settings`, key `rental_showing_rules`. Their changes
go into `rental_listing_changes` with the subject `showing_rules`.

There is no "current version" flag. `rental_listings` holds only the current
state, so nothing that reads it has to choose between versions, and other
tables can point at a listing's row. What a listing looked like on a given day
is its latest row in `rental_listing_changes` on or before that day.

## A listing

| Field | Meaning |
|---|---|
| `unit`, `address` | The unit the listing is for (unique, matched without regard to case) and its address |
| `asking_rent` | The monthly rent asked for the next lease. Not what the current resident pays |
| `next_lease_start_earliest`, `next_lease_start_latest` | When the next lease can start |
| `virtual_tours_allowed` | Whether a virtual tour may be offered |
| `off_market_on` | From this date the unit is no longer on offer, usually because the next lease is signed. Cleared when it goes back on offer |
| `showing_booking_url` | Where a prospect books a showing themselves |
| `preferred_showing_times` | Weekly windows we would like it shown in: a nudge, never a limit |
| `public_facts` | Lines that may be repeated to anyone, as on a listing |
| `internal_notes` | Lines for making decisions that are not repeated outside the team |

The names say which lease and which rent they mean on purpose. A unit let at
1,000 today can be on offer at 1,200, and a field called `rent` would not say
which is meant, to a person or to a model reading it.

Internal notes are for low-stakes things: a resident who wants two days'
notice, a lease start that can move under a condition. The lead agent reads
them and answers strangers, so nothing that would matter if it leaked belongs
in either kind of text.

## Rules for code that changes this

- Go through `service.py`. It checks every save and writes the history row in
  the same transaction.
- `checks.py` refuses values that make no sense and any free text holding a
  phone number, an email address, a link or access-code wording. Rows are
  checked again on read, so a hand edit in the database that breaks a rule is
  refused, not passed on.
- The service does not ask for approval; the caller decides. The general
  agent's tools ([`sernia_ai/tools/rental_listing_tools.py`](../sernia_ai/tools/rental_listing_tools.py))
  require a person's approval for any change to free text or a link.
- Keep imports from the rest of the app few, so this can move out with the
  booking system later. Today it uses the shared `Base` and `app_settings`.

## Not built

An edit screen, HTTP endpoints, and the same tools in the standalone MCP
service (which has no database access, so it would call those endpoints).
