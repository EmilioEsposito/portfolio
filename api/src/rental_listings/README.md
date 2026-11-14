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
├── service.py   the only code that reads and writes them
├── zillow_ad.py fetches a unit's Zillow ad and reads what it says, to compare with the listing
└── routes.py    HTTP endpoints under /api/rental-listings, for the edit screen and other services
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
| `zillow_listing_url_public_view`, `zillow_listing_url_internal_editor_view` | The unit's Zillow ad: the page a lead sees, and the Zillow Rental Manager page where the team edits it. Given by a person; an ad keeps its link from season to season. For the team and the ad check below. The lead agent is not given them |
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
  phone number, an email address, a link or access-code wording. The booking
  link must be plain https; the Zillow links must also be on `zillow.com` or
  a subdomain, since the public one is fetched. Rows are
  checked again on read, so a hand edit in the database that breaks a rule is
  refused, not passed on.
- The service does not ask for approval; the caller decides. The general
  agent's tools ([`sernia_ai/tools/rental_listing_tools.py`](../sernia_ai/tools/rental_listing_tools.py))
  require a person's approval for any change to free text or a link.
- Keep imports from the rest of the app few, so this can move out with the
  booking system later. Today it uses the shared `Base` and `app_settings`.

## Edit screen and endpoints

The Rental Listings page (`/sernia-rental-listings`, linked from the Zillow
Leads Agent architecture page and the Sernia conversation sidebar) edits each listing's fields and the showing rules, and shows the
history. It calls these endpoints, and so does the Sernia MCP service. They
are open to signed-in Sernia users, the same as the admin settings, by two
routes (`rental_listing_caller` in `routes.py`):

- a browser session, from the edit screen;
- a Clerk OAuth access token that a Sernia user gave the Sernia MCP service.
  Only a token issued to the MCP service's own OAuth client is accepted
  (`SERNIA_MCP_CLERK_OAUTH_CLIENT_ID` on this service), and the user's email
  is checked the same way. Without that variable no OAuth token is accepted.

| Method and path | Does |
|---|---|
| `GET /api/rental-listings` | Every listing and the showing rules |
| `POST /api/rental-listings/listings` | Add a listing. 409 if one exists for that unit |
| `PUT /api/rental-listings/listings/{unit}` | Replace a listing. A unit is not renamed |
| `PUT /api/rental-listings/showing-rules` | Replace the showing rules |
| `GET /api/rental-listings/history?subject=&limit=` | Changes, newest first: one unit, `showing_rules`, or everything |
| `GET /api/rental-listings/zillow-ad/{unit}` | What the unit's live Zillow ad says, fetched now from its public link. Saves nothing. 502 with the reason when Zillow cannot be read |

- A write records the signed-in person as `changed_by`, and
  `rental listings screen` or `Sernia MCP` as `source`.
- A write with `?dry_run=true` runs every check, including `based_on`, and
  answers as a save would, but saves nothing. The MCP service uses it so a
  person is not asked to approve a change that would be refused.
- A failed check answers 422 with `detail.problems`, the reasons.
- A write may send `based_on`, the listing or rules as last read. If what is
  on file has changed since, nothing is saved and the answer is 409, so a
  stale screen cannot undo someone else's change.
- A person's own edit needs no approval step. The general agent's tools ask
  for one because the agent may be acting on what a stranger wrote. Here the
  person is the one typing. The checks still apply. The MCP service is a
  model acting for a person, so it asks for approval itself, on the same
  rule as the general agent, before it calls these endpoints.
- A row broken by a hand edit in the database makes the list answer 409 with
  the reasons. Fix it in the database.

## Checking a listing against its Zillow ad

When someone tells the general agent a unit's ad changed, it calls
`rental_listing_zillow_ad_read`. That fetches the unit's
`zillow_listing_url_public_view`, reads the ad (`zillow_ad.py`), and returns it
beside the listing on file. The agent proposes what differs through
`rental_listing_update`, under the usual rule: typed fields such as asking rent
save at once, facts and links wait for approval, and everything passes
`checks.py`. The MCP service has the same tool, through the endpoint above.
It runs on request only. A daily check, if wanted, goes through the general
agent's existing scheduling tools; there is no job for it here.

How the fetch works, and what to expect:

- One plain HTTPS GET with the user agent
  `SerniaListingSync/0.1 (+https://serniacapital.com)`. Zillow serves a named
  crawler a page it marks as served to a bot (`isBot: true`), which still holds
  the listing as JSON: `__NEXT_DATA__` → `props.pageProps.componentProps.gdpClientCache`
  (a JSON string) → the entry with a `property`. A generic client user agent
  (`python-httpx/…`) gets Zillow's "Press & Hold" bot check instead. Checked
  from a home connection on 2026-11-13; whether Zillow answers Railway's
  addresses the same way is not known until it runs there.
- Nothing tries to get past a bot check, and a refusal is not retried. Every
  failure (bot check, no page, a changed page, a page for another listing, a
  redirect off zillow.com) comes back as a reason, and the agent asks the
  person to paste the ad text instead.
- While the ad is live, `homeStatus` is `FOR_RENT` and `price` is the monthly
  rent. Otherwise `price` is Zillow's estimate of the sale value, so `rent` is
  left empty. Zillow holds one availability date
  (`resoFacts.availabilityDate`, milliseconds at midnight UTC); the earliest
  and latest lease start may only be in the description.
- The lead agent does not use this. Its answers should not depend on Zillow
  answering or keeping its page the same; it reads the reviewed listing.

## Sernia MCP

The standalone MCP service has the same five tools as the general agent
(`apps/sernia_mcp/src/sernia_mcp/tools/rental_listings.py`). It has no
database access, so it calls the endpoints above with the signed-in user's
Clerk token. Its approval cards and their rule are described in its
[README](../../../apps/sernia_mcp/README.md#rental-listings).
