# Rental units

What is on file about each rental unit, the showing rules that apply to all
of them, and the history of changes. The Zillow lead agent's case is built
from this ([`sernia_ai/zillow_leads`](../sernia_ai/zillow_leads/README.md)),
and the showing booking system (issue #368) will read it too.

```
rental_units/
├── schema.py    the shapes code depends on: RentalUnit, ShowingRules, PreferredWindow
├── checks.py    what must hold before anything is saved or used
├── models.py    the tables
└── service.py   the only code that reads and writes them
```

## Tables

| Table | One row is |
|---|---|
| `rental_units` | One unit as it stands now. A unit keeps its row from season to season: when it is listed again the rent and dates change and the facts carry over |
| `rental_unit_changes` | One saved change, never updated: who made it, when, from where, and the whole unit (or the showing rules) as it read afterwards |

The showing rules (showing window, blackout dates, shared booking link, time
zone) are one row in `app_settings`, key `rental_showing_rules`. Their changes
go into `rental_unit_changes` with the subject `showing_rules`.

There is no "current version" flag. `rental_units` holds only the current
state, so nothing that reads it has to choose between versions, and other
tables can point at a unit's row. What a unit looked like on a given day is
its latest row in `rental_unit_changes` on or before that day.

## A unit

| Field | Meaning |
|---|---|
| `unit`, `address` | Its name (unique, matched without regard to case) and address |
| `rent`, `lease_start_earliest`, `lease_start_latest`, `virtual_tours` | What is on offer |
| `rented_on` | From this date the unit is taken and no longer offered |
| `booking_url` | Where a prospect books a showing themselves |
| `preferred_times` | Weekly windows we would like it shown in: a nudge, never a limit |
| `public_facts` | Lines that may be repeated to anyone, as on a listing |
| `internal_notes` | Lines for making decisions that are not repeated outside the team |

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
  agent's tools ([`sernia_ai/tools/rental_unit_tools.py`](../sernia_ai/tools/rental_unit_tools.py))
  require a person's approval for any change to free text or a link.
- Keep imports from the rest of the app few, so this can move out with the
  booking system later. Today it uses the shared `Base` and `app_settings`.

## Not built

An edit screen, HTTP endpoints, and the same tools in the standalone MCP
service (which has no database access, so it would call those endpoints).
