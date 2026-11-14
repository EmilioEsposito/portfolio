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
├── zillow_ad.py reads a unit's Zillow ad as text (through Jina Reader), to compare with the listing
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
| `GET /api/rental-listings/zillow-ad/{unit}` | The unit's live Zillow ad as text, read now from its public link. Saves nothing. 424 with the reason when the page cannot be read |

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
`rental_listing_zillow_ad_read`. That reads the page at the unit's
`zillow_listing_url_public_view` as text (`zillow_ad.py`) and returns it beside
the listing on file. The agent proposes every difference in one
`rental_listing_update` call, so a person reviews the whole change on one
approval card, and everything passes `checks.py`. The MCP service has the same tool, through the endpoint above.
It runs on request only. A daily check, if wanted, goes through the general
agent's existing scheduling tools; there is no job for it here.

How the page is read, and what to expect:

- Zillow refuses requests from Railway's addresses: a direct GET from the
  development environment got its "Press & Hold" bot check (HTTP 403) on
  2026-11-14, with the same named user agent that got the full page from a
  home connection. So the page is read through
  [Jina Reader](https://jina.ai/reader/): `GET https://r.jina.ai/<zillow url>`
  with `X-Return-Format: markdown`, `X-No-Cache: true` and
  `X-Retain-Images: none`. Jina renders the page in a browser on its servers
  (7 to 13 seconds in testing) and returns Markdown. No key is needed (20
  requests a minute); `JINA_API_KEY`, when set, is sent as a bearer token for
  higher limits. Jina's browser-rendered HTML, which would carry Zillow's
  listing JSON, needs a key, so the Markdown is used.
- `zillow_ad.py` keeps only the listing: the lines above the address heading
  (rent per month, availability) and the "What's special", "Facts &
  features" and "Price history" sections, without links or images. About
  2,000 characters for a live ad. A page with no live ad says "Off market"
  and shows a Zestimate and "Estimated rent" instead of a monthly rent.
- Nothing handles a CAPTCHA and a refusal is not retried. Every failure (no
  link on file, the reader busy or refused, a page for another listing, a
  changed page) comes back as a reason, and the agent asks the person to
  paste the ad text instead. The endpoint answers 424 with the reason; not
  502, which Cloudflare would replace with its own page.
- The reader sees only the public listing URL. Zillow's terms do not allow
  automated access; this reads our own ads, a few times a season, on request.
- The lead agent does not use this. Its answers should not depend on Zillow
  answering or keeping its page the same; it reads the reviewed listing.

## Sernia MCP

The standalone MCP service has the same five tools as the general agent
(`apps/sernia_mcp/src/sernia_mcp/tools/rental_listings.py`). It has no
database access, so it calls the endpoints above with the signed-in user's
Clerk token. Its approval cards and their rule are described in its
[README](../../../apps/sernia_mcp/README.md#rental-listings).
