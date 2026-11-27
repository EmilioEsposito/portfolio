# Portfolio web app

React Router v8 framework mode with React 19, Tailwind, Clerk (for the Sernia team's
pages only), and a FastAPI backend.

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

## Layout

One build serves eesposito.com and serniacapital.com, so the code is laid out by
the site it serves. `~/` resolves to `app/`.

| Domain | Serves | Sign-in |
|---|---|---|
| eesposito.com | The portfolio: home, AI demos, `/systems/*` write-ups (`/systems/leasing-leads` included), `/sitemap.xml`, `/robots.txt`. Everything else is the portfolio's 404, `/team` included. | None. No Clerk script and no Clerk request. |
| serniacapital.com | Sernia's public pages (`/`, `/showings`, `/showings/*`, `/privacy`, `/sms-terms`), `/robots.txt` (disallows `/team`), and the team's pages under `/team`. Everything else is the Sernia 404. `www.serniacapital.com/team...` redirects (308) to the same address on `serniacapital.com`; the public pages answer on `www` as they are. | `/team` only. |
| dev.eesposito.com, PR previews, localhost | The portfolio, the Sernia public pages at their paths, and `/team`, since the team's pages are served on any host outside production. | `/team` only. |

```text
app/
  root.tsx                  the document and each site's shell
  routes.ts                 every URL, grouped by site
  sites/
    site.ts                 which site answers a request (server.js and vite.config.ts use it too)
    root-request.server.ts  the root middleware and loader, per site
    sernia-root.ts          which site a page is, as the browser reads it
    home.tsx                `/`: the Sernia home page on a Sernia host, the portfolio's elsewhere
    not-found.tsx           unknown URLs, so a Sernia host answers its own 404
    robots.txt.ts           robots.txt, by host
    portfolio/              eesposito.com: public pages, AI demos, /systems write-ups; no Clerk
    sernia-public/          serniacapital.com: no sign-in and no Clerk
    team/                   serniacapital.com/team: the signed-in Sernia staff pages
  components/ lib/ hooks/   code more than one site uses
```

Each site folder holds `routes/`, `components/` and, where needed, `lib/` and
`hooks/`. A file one site uses lives under that site; a file more than one site
uses lives in `app/components`, `app/lib` or `app/hooks`. A site never imports
another site's folder; the files directly under `app/sites/` may import any site.
Routes are listed in `app/routes.ts`, not discovered from file names.
`test/lib/site-layout.test.ts` checks that no site imports another's folder, that
nothing the portfolio or the Sernia public pages import loads Clerk, and that
every route module in a site's `routes/` is listed in `app/routes.ts`.
`test/lib/site-rule.test.ts` checks that every team route is under `/team` inside
the team layout and that nothing else is.

## The team's pages (`/team`)

The Sernia team's signed-in pages, at fixed addresses the backend links to
(`api/src/utils/team_links.py`). Old staff addresses (`/sernia-chat`,
`/message-tenants`, `/systems/leasing-leads/bookings` and so on) do not exist
and have no redirects.

| Page | Address |
|---|---|
| Tasks (My Tasks, all team tasks, and ClickUp creation) | `/team/tasks` |
| Team home: approvals waiting, open tasks, every team page, the notifications prompt | `/team` |
| Sernia AI chat (`?id=<conversation>`) | `/team/chat` |
| General agent settings | `/team/settings` |
| Push devices (admin only) | `/team/push-devices` |
| All conversations | `/team/admin` |
| Message tenants | `/team/tenants` |
| Scheduler | `/team/scheduler` |
| Lead agent: overview, listing data, agent settings, booking settings | `/team/leads`, `/team/leads/listings`, `/team/leads/settings`, `/team/leads/bookings` |

- Where: on a Sernia host always, whether or not the public pages are switched
  on, and on any other host only outside production (`RAILWAY_ENVIRONMENT_NAME`
  is not `production`). In production a team path on another host answers the
  portfolio's 404.
- Sign-in is scoped to `/team`. The root route (`app/root.tsx`, through
  `app/sites/root-request.server.ts`) runs `clerkMiddleware` and
  `rootAuthLoader` on the server for team requests and nothing else. The
  team's layout (`app/sites/team/routes/layout.tsx`) renders `<ClerkProvider>`
  from the root loader's data, so only team pages download Clerk's code. Clerk's keys come
  from the environment (`CLERK_SECRET_KEY`, `VITE_CLERK_PUBLISHABLE_KEY`); its
  production instance has to be on serniacapital.com for sign-in to work there.
- The team layout (`app/sites/team/routes/layout.tsx`) holds the one guard: a
  signed-in user with a verified `@serniacapital.com` address gets in. Signed
  out, the page is a Sernia sign-in page (Clerk's sign-in window), which the
  server renders from Clerk's state before Clerk's script loads. Any other
  account is told so and can sign out. The API checks every request on its own.
  The decision is `app/sites/team/lib/team-access.ts`; the pages it shows are
  `app/sites/team/components/team-gate.tsx`; both are tested without Clerk.
- Navigation: most pages get the team sidebar (`team-nav.tsx`, built from the
  sidebar primitives in `app/components/ui/sidebar.tsx`) with the Sernia
  wordmark, every team page, the account button and a link to the public site.
  The chat, its settings and the conversations table keep their conversation
  sidebar, which holds the same links. A page that brings its own sidebar sets
  `handle = { ownSidebar: true }`. General agent settings and all
  conversations show only to the admin, as before.
- Team pages are `noindex`, and robots.txt on a Sernia host disallows `/team`.
- Approvals and open tasks: the team layout reads `GET /api/sernia-ai/workflow/pending`
  (conversations among the 50 most recent with a step waiting for approval) and
  `GET /api/team-tasks` once, in `components/team-counts.tsx`. The navigation
  counts the signed-in person's open assignments for the Tasks badge; the team home shows them on
  those two cards and lists the conversations to approve while there are any;
  the chat's conversation list and the conversations table mark those
  conversations "To approve" (the history list's own `has_pending` is only
  filled for the pending list). They are read again on the next page opened or
  when the app returns to the screen, once a minute at most, at once on the
  team home and after a change in the chat; the Tasks page hands over its own
  list instead of a second read.
- The lead agent overview (`/team/leads`) and the portfolio's write-up
  (`/systems/leasing-leads`) render the same `app/components/leasing-overview.tsx`.
- Installed app and notifications: the manifest and service worker are
  `public/team/manifest.json` and `public/team/sw.js`, so the worker's scope is
  `/team/` and the installed app opens the team home, `/team`. A link the
  system hands to the installed app reuses its open window
  (`launch_handler`). Only team pages link the manifest and carry the
  home-screen app tags. Portfolio and team pages unregister the worker the
  staff app used to register at the site root, and drop its push subscription
  (`app/lib/retire-root-service-worker.ts`).

To open the team's pages locally, run `pnpm dev` (or `pnpm dev-with-fastapi`)
and go to `http://localhost:<web port>/team` or
`http://serniacapital.localhost:<web port>/team`. Sign-in uses the development
Clerk instance in `apps/web-react-router/.env`.

## Public experiences

- `/`: portfolio and selected work.
- `/multi-agent-chat`: agent showcase with streamed execution activity.
- `/systems/leasing-leads`: Zillow Leads Agent architecture, who is told what about a showing, what is left to people, and evaluation links. Public and server-rendered, with no sign-in; a line points Sernia staff to `https://serniacapital.com/team`.
- `/calendly`: meeting scheduling.

The team's Booking settings page (`/team/leads/bookings`) covers who can book, what a
booking sends, captured messages and sending them by hand, the messages' history, calendar
connections, showing agents and showings (`app/sites/team/components/showing-bookings/`; what it
decides is in `app/sites/team/lib/showing-bookings.ts`, unit tested). Its loader says whether this
environment serves the public booking pages (`SERNIA_PUBLIC_PAGES_ENABLED`), which the API does not know.

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
this same service. They need no sign-in, and they load no Clerk at all; only the
team's pages under `/team` do.

- Paths: `/showings`, `/showings/*`, `/privacy` and `/sms-terms` on any host, and
  the home page at `/` on a Sernia host only. There are `/privacy`, `/sms-terms`,
  the units list `/showings` and the booking page `/showings/:unit` (below).
  Pages live in `app/sites/sernia-public/routes/`
  under a layout with its own header and footer. Tests check that each page there
  sits at a Sernia public path, and that every page at such a path is under the
  layout, whose middleware enforces the switch below.
- `/` is the one page that depends on the host. `app/sites/home.tsx` is the
  only route there: on a Sernia host while the pages are on, its loader and
  component serve the Sernia home page (`app/sites/sernia-public/components/home.tsx`),
  without Clerk; on every other host it is the personal site's home page
  (`app/sites/portfolio/components/home.tsx`) as before. With the pages
  off, `/` on a Sernia host is the Sernia 404 like every other path there. The
  personal home page's canonical link moved from `links` to `meta`, so the Sernia
  home page does not carry it.
- Switch: `SERNIA_PUBLIC_PAGES_ENABLED`, read on every request. In production
  (`RAILWAY_ENVIRONMENT_NAME` is `production`) the pages are off unless it is
  `true`; elsewhere they are on unless it is `false`. While they are off, their
  paths on other hosts answer exactly as an unknown URL does (the portfolio's
  404), and a Sernia host answers the Sernia 404 for every path but `/team` and
  `/robots.txt`.
- Hosts: `serniacapital.com`, `www.serniacapital.com` and `serniacapital.localhost`.
  `SERNIA_PUBLIC_HOSTS` (comma separated) replaces that list on a deployed server.
  The managed dev launcher passes only allowlisted variables, so neither variable
  reaches `pnpm dev`; locally the pages are on and the default hosts apply.
- `app/sites/site.ts` makes every decision, and
  `app/sites/root-request.server.ts` applies it in the root middleware and loader.
  Only a team request runs `clerkMiddleware` and `rootAuthLoader`, and only
  the team's layout renders `<ClerkProvider>`. Previews and localhost show the pages at the same paths.
- On a Sernia host every other path (but `/team` and `/robots.txt`) answers 404
  with the Sernia layout. `app/sites/not-found.tsx` sends unknown URLs through the
  root middleware so this also holds for them; other hosts get the portfolio's 404.
- On a Sernia host the `/api` proxy forwards the public booking API and the
  team's API paths only; see "Configuration and API proxy" below.

To check locally, open `http://serniacapital.localhost:<web port>/` or `/privacy`.
Browsers resolve `*.localhost` to this machine without any hosts-file change.

### The shell

`app/sites/sernia-public/components/shell.tsx` is the header and footer of every Sernia
page, the 404 included. The wordmark links to `/` only where `/` is the Sernia
home page: the server says so in the root loader's data, or in the 404 it throws
(`homeAtRoot`, read by `app/sites/sernia-root.ts`). The `sernia-public` class it sets
carries the brand colors and the font (`app/app.css`), and changes the theme's text
and line colors for these pages, in light and dark. Sky (`#5bbdee`) is for drawings
and underlines, never for text.

The root layout gives these pages their own icons (`public/images/sernia/`) and
loads Figtree from Google Fonts; the portfolio keeps its icon and Inter, and the
team's pages use Sernia's icons with Inter and the install manifest.
`Figtree fallback` in `app.css` is Arial with Figtree's measurements, so the text
does not move when the font arrives.

The header and the footer have a quiet Team link to the team's pages: `/team`
where this host serves them, `https://serniacapital.com/team` from production
eesposito.com (`teamHref` in the root data and the Sernia 404). It is a full
page load, since the team's pages run Clerk.

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
`app/sites/sernia-public/lib/showing-units.server.ts`). Off, slow or failing, the page renders at
once without the call line. The footer links to the SMS terms on the other pages
only.

`/showings` lists the units on offer from `GET /api/showing-bookings/public/units`,
read on the server (`app/sites/sernia-public/lib/showing-units.server.ts`): address, unit, asking
rent and lease start, each row a link to the unit's booking page, in address
order. The lease start comes worded by the API, by the same rule as the booking
form's tick. The page is indexable, with its canonical on serniacapital.com.

The API takes a second or two to answer with the units, so nothing a visitor
clicks waits for it. A line across the top shows from the click until the next page
opens (`OpeningLine` in `shell.tsx`), and the list page opens before its list is
read: the list follows in the same response, after a placeholder (a bot gets the
whole page at once). Each page also starts the read for the next one
(`app/sites/sernia-public/lib/read-ahead.server.ts`): the home page the list, the list its first six
booking pages. A navigation inside the site uses an answer up to a minute old; a
page load always asks the API. A booking is checked again when it is made, so an
answer a minute old cannot book a time that has gone.

While public booking is off the API answers 404: `/showings` says no units are
open for online booking right now, and the home page leaves out the call line,
whose number route is off too. With booking on and no unit on offer, the list
says so above the call line. Row wording is in `app/sites/sernia-public/lib/showing-units.ts`.

### The booking page

`/showings/:unit` books a 30-minute in-person showing through the public
booking API (`api/src/showing_bookings/README.md`). Its loader reads the unit on
the server (`app/sites/sernia-public/lib/backend-url.server.ts` picks the backend as `server.js`
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
(times in the unit's time zone, refusals) is in `app/sites/sernia-public/lib/showing-booking-page.ts`
and tested in `test/lib/showing-booking-page.test.ts`.

### Turnstile

Each booking and each "Show phone number" sends its own Turnstile token, since a
token works once. The check takes a few seconds, so one runs ahead of time, once
the page is idle or at the first sign of use, and its token is held for the click
that needs it (`app/sites/sernia-public/components/turnstile.tsx`, and
`app/sites/sernia-public/lib/turnstile-tokens.ts` for the holder, which the call line and the form
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
`team-area.spec.ts` checks the team's sign-in page and 404 on the Sernia host,
the Team link, that portfolio and Sernia public pages make no request to Clerk,
the portfolio's `/systems/leasing-leads` without tabs, and the proxy rule. The
web server gets made-up Clerk keys whose Clerk host does not exist, so every team
page is signed out and Clerk's script is blocked: what a signed-in team member
sees is not covered.
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

Both proxies apply one rule, `isApiProxyAllowed` in `app/sites/site.ts`. Other
hosts forward every `/api` path. A Sernia host forwards only the public booking
API (`/api/showing-bookings/public/`) and the paths the team's pages call
(`TEAM_API_PATHS`: `/api/sernia-ai/`, `/api/showing-bookings/` including the
calendar callback Google sends the browser to, `/api/rental-listings`,
`/api/zillow-leads/settings`, `/api/contacts/slug/sernia`,
`/api/open_phone/tenants`, `/api/open_phone/send_message` and
`/api/schedulers/`), checked as received and after resolving `..`;
anything else answers 404. A new API call from a team page needs its path added
there. `server.js` imports the rule as TypeScript, which Node 22.18+ strips at
load, and the Dockerfile copies it into the image. `server.js` serves
`build/client` without redirecting a folder to its trailing slash, because
`build/client/team/` holds the service worker and `/team` is a page.

See [AI showcase backend documentation](../../api/src/portfolio/README.md) for
model configuration, execution events, and abuse controls.
