# React Router v7 Web App

> **Status**: 🚧 In Development - Proof of Concept
>
> This is a parallel implementation using React Router v7 as we migrate away from Next.js.

## Quick Start

```bash
# From repo root
cd apps/web-react-router

# Install dependencies (uses pnpm workspaces)
pnpm install

# Start dev server
pnpm dev
```

Visit: http://localhost:5173

## What's Implemented

- ✅ Basic React Router v7 setup with file-based routing
- ✅ Clerk authentication integration
- ✅ Tailwind CSS v4
- ✅ TypeScript with generated route types
- ✅ Test page migration (proof of concept)

## Project Structure

```
app/
├── routes/
│   ├── home.tsx        # Index route (/)
│   └── test.tsx        # /test (migrated from Next.js)
├── routes.ts           # Route configuration
├── root.tsx            # Root layout with Clerk
├── app.css             # Global styles
└── welcome/            # Welcome screen components

public/                 # Static assets
react-router.config.ts  # Framework configuration
.env                    # Environment variables (VITE_ prefix)
```

## Key Features

### File-Based Routing
Routes are defined in `app/routes.ts` and point to route modules:

```typescript
export default [
  index("routes/home.tsx"),       // /
  route("test", "routes/test.tsx"), // /test
] satisfies RouteConfig;
```

### Authentication (Clerk)
Integrated at the root level with middleware:

```typescript
// app/root.tsx
export const middleware: Route.MiddlewareFunction[] = [clerkMiddleware()];
export const loader = (args: Route.LoaderArgs) => rootAuthLoader(args);
```

### TypeScript
Route modules get auto-generated types:

```typescript
import type { Route } from "./+types/test";

export function meta({}: Route.MetaArgs) { /* ... */ }
export function loader({ request }: Route.LoaderArgs) { /* ... */ }
export default function TestPage({ loaderData }: Route.ComponentProps) { /* ... */ }
```

## Environment Variables

Create a `.env` file (already exists) with:

```env
VITE_CLERK_PUBLISHABLE_KEY="pk_test_..."
CLERK_SECRET_KEY="sk_test_..."
```

**Note**: Vite uses `VITE_` prefix for client-side variables, unlike Next.js which uses `NEXT_PUBLIC_`.

## Available Scripts

```bash
pnpm dev        # Start dev server (port 5173)
pnpm build      # Build for production
pnpm start      # Start production server
pnpm typecheck  # Run TypeScript type checking
```

> **Note**: This app uses pnpm as the package manager (consistent with the monorepo). Do not use npm or yarn.

## Migration Status

See [MIGRATION.md](./MIGRATION.md) for detailed migration guide and patterns.

### Migrated Pages
- [x] `/` - Homepage with full portfolio content
- [x] `/test` - Simple test page
- [x] `/chat-emilio` - AI chat with FastAPI backend integration ✨
- [x] `/calendly` - Schedule meeting with embedded Calendly widget
- [x] `/multi-agent-chat` - Multi-agent AI assistant (routes to Emilio/Weather agents)
- [x] `/chat-weather` - AI Weather agent chat

### Migrated Components
- [x] Typography components (H1, H2, H3, P, Lead, Large, Small, Muted)
- [x] Button component (Shadcn UI)
- [x] Textarea component (Shadcn UI)
- [x] Weather component (for tool results display)
- [x] cn() utility function
- [x] Path aliases configured (`~/` -> `./app/`)
- [x] useScrollToBottom hook
- [x] AI SDK integration (`ai@5.0.92` with streaming support)

### Pending Migration
- [ ] `/scheduler` - Job management
- [ ] `/tenant-mass-messaging` - Bulk SMS
- [ ] `/ai-email-responder` - Email automation
- [ ] Additional Shadcn UI components as needed

## Development Notes

### Hot Module Replacement (HMR)
React Router's HMR is noticeably faster than Next.js. Changes reflect instantly without full page reload in most cases.

### No More `page.tsx`
Files can have descriptive names instead of all being `page.tsx`. Much better for searching and navigation!

### Server vs Client Code
- Default: Client-side React components
- Use `.server.tsx` extension for server-only code
- Use loaders/actions for server-side data fetching

### Framework Mode Features
- **Loaders**: Fetch data before rendering (like Next.js Server Components)
- **Actions**: Handle form submissions and mutations
- **Middleware**: Process requests before they reach routes
- **SSR**: Server-side rendering enabled by default

## Differences from Next.js

| Feature | Next.js | React Router |
|---------|---------|--------------|
| File naming | `page.tsx` everywhere | Descriptive names |
| Client components | `"use client"` directive | Default behavior |
| Server data | Async components | `loader` function |
| Forms | Server Actions | `action` function |
| Env vars | `NEXT_PUBLIC_*` | `VITE_*` |
| Port | 3000 | 5173 |
| Speed | Slower HMR | Faster HMR |

## Testing

The app uses Playwright for E2E testing (to be set up). Run tests with:

```bash
# From repo root
pnpm test:e2e
```

## API Proxy

All `/api/*` requests are proxied to the FastAPI backend:

| Environment | Proxy Handler | Backend URL |
|-------------|---------------|-------------|
| Dev (`pnpm dev`) | `vite.config.ts` | `http://127.0.0.1:8000` |
| Railway | `server.js` | `CUSTOM_RAILWAY_BACKEND_URL` |
| Docker Compose | `server.js` | `http://fastapi:8000` (via `LOCAL_DOCKER_COMPOSE=true`) |
| Local prod | `server.js` | `http://127.0.0.1:8000` |

**Usage**: Just use relative URLs in your code:
```typescript
// Client-side fetch - works in both dev and prod
const response = await fetch('/api/health');
```

**Files**:
- `vite.config.ts` - Dev proxy config
- `server.js` - Production Express server with proxy

## Deployment

Deployed on Railway with custom Express server (`server.js`).

**Env vars**:
- `CUSTOM_RAILWAY_BACKEND_URL` - Railway internal FastAPI URL
- `LOCAL_DOCKER_COMPOSE` - Set in docker-compose.yml to use `http://fastapi:8000`

## Documentation

- [MIGRATION.md](./MIGRATION.md) - Migration guide and patterns
- [React Router Docs](https://reactrouter.com/start/framework/installation)
- [Clerk React Router](https://clerk.com/docs/react-router/getting-started/quickstart)

## Questions?

See the migration guide or refer to:
- Main portfolio README: `/README.md`
- CLAUDE.md: `/CLAUDE.md` (AI assistant guide)
