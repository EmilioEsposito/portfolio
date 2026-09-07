# Portfolio web app

React Router v7 framework mode with React 19, Tailwind, Clerk, and a FastAPI backend.

## Development

Use Node 22.13+ and the pnpm version pinned in the root `package.json`.
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
- `/ai-email-responder`: email studio using fictional scenarios; never reads or sends real email.
- `/calendly`: meeting scheduling.

The former standalone career/weather chat URLs redirect to the agent showcase.
The retired `/examples/*` and `/test` routes have been removed. The backend's example
REST and GraphQL endpoints have also been removed. Historical database migrations
and inactive table metadata remain so this UI cleanup does not delete stored data.

## Structure

File-based routes live in `app/routes/`, discovered by `flatRoutes()` in `app/routes.ts`.
Shared components live in `app/components/`; `~/` resolves to `app/`.
The root layout configures Clerk and theme support. Operator pages retain their
existing authorization requirements.

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
