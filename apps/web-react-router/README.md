# Portfolio web app

React Router v8 framework mode with React 19, Tailwind, Clerk, and a FastAPI backend.

## Development

Use the Node version in `.nvmrc` and the pnpm version pinned in the root `package.json`.
From the repository root:

```bash
pnpm dev                 # Frontend
pnpm dev-with-fastapi    # Frontend and backend
pnpm --filter web-react-router typecheck
pnpm --filter web-react-router test
pnpm build
```

For isolated development, follow [the worktree guide](../../docs/WORKTREES.md).
The provisioner prints the assigned preview URL and configures the API proxy.

## Public experiences

- `/`: portfolio and selected work.
- `/multi-agent-chat`: agent showcase with streamed execution activity.
- `/systems/leasing-leads`: Zillow Leads Agent architecture, listing-data flow, planned booking integration, and evaluation links. Public and server-rendered; verified Sernia users also see management links.
- `/systems/leasing-leads/listings`: authenticated rental listing data tab; no conversation pane.
- `/systems/leasing-leads/settings`: dedicated lead batching and email-approval settings.
- `/systems/leasing-leads/bookings`: authenticated Booking settings tab: who can book, what a
  booking sends, captured messages and sending them by hand, the messages' history, calendar
  connections, showing agents and showings (`app/components/showing-bookings/`; what it decides is
  in `app/lib/showing-bookings.ts`, unit tested). Its loader says whether this environment serves
  the public booking pages (`SERNIA_PUBLIC_PAGES_ENABLED`), which the API does not know.

The public architecture and these operator pages share route-backed navigation
tabs. `/sernia-rental-listings` redirects to the listing tab; the legacy
`/sernia-settings#zillow-leads` link redirects to Agent settings.
- `/calendly`: meeting scheduling.

The retired `/ai-email-responder` and `/ai-email-responder/architecture` URLs
permanently redirect to `/systems/leasing-leads`. Their fictional drafting API
and fixtures have been removed. The public chat showcase retains its budget
and credential controls.

The former standalone career/weather chat URLs redirect to the agent showcase.
The retired `/examples/*` and `/test` routes have been removed. The backend's example
REST and GraphQL endpoints have also been removed. Historical database migrations
and inactive table metadata remain so this UI cleanup does not delete stored data.

## Sernia public pages

Sernia Capital's public pages (issue #368) are served on serniacapital.com by
this same service. They need no sign-in, and they load no Clerk at all, because
Clerk's production keys belong to eesposito.com.

- Paths: `/showings`, `/showings/*`, `/privacy` and `/sms-terms` on any host, and
  the home page at `/` on a Sernia host only. There are `/privacy`, `/sms-terms`,
  the units list `/showings` and the booking page `/showings/:unit` (below).
  Pages live in `app/routes/_sernia-public.*.tsx`
  under a layout with its own header and footer. Tests check that each page there
  sits at a Sernia public path, and that every page at such a path is under the
  layout, whose middleware enforces the switch below.
- `/` is the one page that depends on the host. `app/routes/_index.tsx` is the
  only route there: on a Sernia host while the pages are on, its loader and
  component serve the Sernia home page (`app/components/sernia-public/home.tsx`),
  without Clerk; on every other host it is the personal site's home page as before. With the pages
  off, `/` on a Sernia host is the Sernia 404 like every other path there. The
  personal home page's canonical link moved from `links` to `meta`, so the Sernia
  home page does not carry it.
- Switch: `SERNIA_PUBLIC_PAGES_ENABLED`, read on every request. In production
  (`RAILWAY_ENVIRONMENT_NAME` is `production`) the pages are off unless it is
  `true`; elsewhere they are on unless it is `false`. While they are off, their
  paths on other hosts answer exactly as an unknown URL does (the app's path
  through Clerk, then the personal 404), and a Sernia host answers the Sernia 404
  for every path.
- Hosts: `serniacapital.com`, `www.serniacapital.com` and `serniacapital.localhost`.
  `SERNIA_PUBLIC_HOSTS` (comma separated) replaces that list on a deployed server.
  The managed dev launcher passes only allowlisted variables, so neither variable
  reaches `pnpm dev`; locally the pages are on and the default hosts apply.
- `app/lib/sernia-public.ts` makes every decision, and
  `app/lib/root-request.server.ts` applies it in the root middleware and loader.
  A request to a Sernia host, or to a Sernia public path on any host while the
  pages are on, skips `clerkMiddleware`, `rootAuthLoader` and `<ClerkProvider>`.
  Previews and localhost therefore show the pages at the same paths.
- On a Sernia host every other path answers 404 with the Sernia layout.
  `app/routes/$.tsx` sends unknown URLs through the root middleware so this also
  holds for them; other hosts get the same 404 page as before.
- On a Sernia host the `/api` proxy forwards only `/api/showing-bookings/public/`
  and answers 404 for anything else, in `server.js` and in `vite.config.ts`,
  whether or not the pages are on. `server.js` imports the helper as TypeScript,
  which Node 22.18+ strips at load, and the Dockerfile copies it into the image.

To check locally, open `http://serniacapital.localhost:<web port>/` or `/privacy`.
Browsers resolve `*.localhost` to this machine without any hosts-file change.

### The shell

`app/components/sernia-public/shell.tsx` is the header and footer of every Sernia
page, the 404 included. The wordmark links to `/` only where `/` is the Sernia
home page: the server says so in the root loader's data, or in the 404 it throws
(`homeAtRoot`, read by `app/lib/sernia-root.ts`). The `sernia-public` class it sets
carries the brand colors and the font (`app/app.css`), and changes the theme's text
and line colors for these pages, in light and dark. Sky (`#5bbdee`) is for drawings
and underlines, never for text.

The root layout gives these pages their own icons (`public/images/sernia/`) and
loads Figtree from Google Fonts; the other pages keep theirs, Inter and the install
manifest. `Figtree fallback` in `app.css` is Arial with Figtree's measurements, so
the text does not move when the font arrives.

### The home page and the units list

Resident sign-in (the residents' Buildium portal) is in the home page's header,
since residents type the domain to reach it. The page has Book a showing
(`/showings`), Apply (`/apply`, which stays a Cloudflare redirect, so it has no page
here and 404s on previews and localhost), the call line, the street drawing
(`drawings.tsx`, static and decorative), one photo and a section for residents. The
photo is in `public/images/sernia/` at two widths, as WebP with a JPEG fallback;
`og.png` there is the link preview.
The page never waits on the units list: its loader waits only for
`GET /api/showing-bookings/public/call-line` (the Turnstile site key, no listings
or calendars) with a 3-second timeout (`readCallLine` in
`app/lib/showing-units.server.ts`). Off, slow or failing, the page renders at
once without the call line. The footer links to the SMS terms on the other pages
only.

`/showings` lists the units on offer from `GET /api/showing-bookings/public/units`,
read on the server (`app/lib/showing-units.server.ts`): address, unit, asking
rent and lease start, each row a link to the unit's booking page, in address
order. The lease start comes worded by the API, by the same rule as the booking
form's tick. The page is indexable, with its canonical on serniacapital.com.

The API takes a second or two to answer with the units, so nothing a visitor
clicks waits for it. A line across the top shows from the click until the next page
opens (`OpeningLine` in `shell.tsx`), and the list page opens before its list is
read: the list follows in the same response, after a placeholder (a bot gets the
whole page at once). Each page also starts the read for the next one
(`app/lib/read-ahead.server.ts`): the home page the list, the list its first six
booking pages. A navigation inside the site uses an answer up to a minute old; a
page load always asks the API. A booking is checked again when it is made, so an
answer a minute old cannot book a time that has gone.

While public booking is off the API answers 404: `/showings` says no units are
open for online booking right now, and the home page leaves out the call line,
whose number route is off too. With booking on and no unit on offer, the list
says so above the call line. Row wording is in `app/lib/showing-units.ts`.

### The booking page

`/showings/:unit` books a 30-minute in-person showing through the public
booking API (`api/src/showing_bookings/README.md`). Its loader reads the unit on
the server (`app/lib/backend-url.server.ts` picks the backend as `server.js`
does), so a unit not on offer, or booking switched off, is a 404 page. The
booking, a corrected mobile and the team's number go from the browser straight
to `/api/showing-bookings/public/...`, so the API sees the person's own IP
address, user agent and `Signature-Agent` header. The ticks' wording and the
small print come from the API. With a lead's own link (`?t=`), the loader also
asks the API for the place booked in their email thread: the page then asks only
for their details, or, once they are in, says "You're booked" with when and
where, and shows the call line for someone the text never reached. It holds no
cancel link, since a lead's link can be forwarded. That link works for a unit that
is no longer open to others: the unit answers 404, and the loader reads what the
page shows from `lead-booking/unit`. The page says "We texted" a number only once
the API says the text went (`texted`), and "We'll text" while it is captured or
waiting. The page
is `noindex` for now. Pure logic
(times in the unit's time zone, refusals) is in `app/lib/showing-booking-page.ts`
and tested in `test/lib/showing-booking-page.test.ts`.

### Turnstile

Each booking and each "Show phone number" sends its own Turnstile token, since a
token works once. The check takes a few seconds, so one runs ahead of time, once
the page is idle or at the first sign of use, and its token is held for the click
that needs it (`app/components/sernia-public/turnstile.tsx`, and
`app/lib/turnstile-tokens.ts` for the holder, which the call line and the form
share). A held token counts as expired after 4 of its 5 minutes. A check ahead of
time runs in an element no one can see or focus, fails silently, and is dropped if
it would ask the person something; their own check then runs beside what they
pressed. Outside production the API uses Cloudflare's test keys and answers the
test secret itself.

### Browser test

`test/e2e/` holds the repository's browser test (Playwright, Chromium only): book a
showing, miss a tick, correct the mobile, cancel from the confirmation screen, an
unknown unit, the way from the Sernia home page through the units list to a
booking page, a lead adding their details to a showing the lead agent booked by
email (the script's `lead-booking` command books it), and their link saying
they are booked afterwards, at the new time once a reply moves it (`lead-move`),
and working for a unit closed to others (`close-units`), and the bot check ahead of time
(the number without a new check, a check that needs the person, a blocked one).
It builds the app, starts the API and `node server.js` with a clean environment (`test/e2e/setup.ts`:
made-up keys only, no secret, capture mode), opens the page on
`serniacapital.localhost`, answers Turnstile's script in the browser and blocks
every other request that would leave the machine. Before each test
`scripts/showing_browser_test.py reset` empties the showing tables and saves a
made-up listing, and the tests read the rows back with its `rows` command. The
script refuses production and any database not on this machine.

Run it from the repository root against a disposable local database (the CI job
`Booking page in the browser` does the same with a Postgres service):

```bash
psql postgresql://portfolio:portfolio@127.0.0.1:5432/postgres -c 'CREATE DATABASE portfolio_e2e'  # once
pnpm --filter web-react-router exec playwright install chromium                                    # once
pnpm --filter web-react-router test:e2e
```

`E2E_DATABASE_URL` (default `postgresql://portfolio:portfolio@127.0.0.1:5432/portfolio_e2e`),
`E2E_API_PORT` (8765) and `E2E_WEB_PORT` (5765) change the defaults, for example
to run two worktrees at once. A failure keeps a trace and a screenshot in
`test-results/`; open a trace with `pnpm --filter web-react-router exec playwright show-trace <trace.zip>`.
In CI they are uploaded as the `browser-test-results` artifact.

## Structure

File-based routes live in `app/routes/`, discovered by `flatRoutes()` in `app/routes.ts`.
Shared components live in `app/components/`; `~/` resolves to `app/`.
The root layout configures Clerk and theme support, except for the Sernia public
pages above. Operator pages retain their existing authorization requirements.

## Configuration and API proxy

Frontend credentials belong in `apps/web-react-router/.env`; backend credentials
belong in the root `.env`. Never expose inference API keys through `VITE_` variables.
Use the repository's provisioning scripts to configure a worktree.

All browser API requests use relative `/api/*` URLs:

| Environment | Proxy | Destination |
|---|---|---|
| Development | `vite.config.ts` | Worktree API port, or localhost:8000 |
| Railway | `server.js` | `CUSTOM_RAILWAY_BACKEND_URL` |
| Docker Compose | `server.js` | `http://fastapi:8000` |

See [AI showcase backend documentation](../../api/src/ai_demos/README.md) for
model configuration, execution events, and abuse controls.
