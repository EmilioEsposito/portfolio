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
- `/systems/leasing-leads/bookings`: authenticated booking settings placeholder.

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

Sernia Capital's public pages (issue #368) will be served on serniacapital.com by
this same service. They need no sign-in, and they load no Clerk at all, because
Clerk's production keys belong to eesposito.com.

- Paths: `/showings`, `/showings/*`, `/privacy` and `/sms-terms`. Only `/privacy`
  and `/sms-terms` exist so far. Pages live in `app/routes/_sernia-public.*.tsx`
  under a layout with its own header and footer. Tests check that each page there
  sits at a Sernia public path, and that every page at such a path is under the
  layout, whose middleware enforces the switch below.
- Switch: `SERNIA_PUBLIC_PAGES_ENABLED`, read on every request. In production
  (`RAILWAY_ENVIRONMENT_NAME` is `production`) the pages are off unless it is
  `true`; elsewhere they are on unless it is `false`. While they are off, their
  paths on other hosts answer exactly as an unknown URL does (the app's path
  through Clerk, then the personal 404), and a Sernia host answers the Sernia 404
  for every path. It is not set anywhere: turning the pages on in production
  waits for Emilio to approve their wording.
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

To check locally, open `http://serniacapital.localhost:<web port>/privacy`.
Browsers resolve `*.localhost` to this machine without any hosts-file change.

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
