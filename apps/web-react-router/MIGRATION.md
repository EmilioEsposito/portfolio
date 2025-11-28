# Next.js to React Router v7 Migration Guide

> **Created**: 2025-11-27
> **Updated**: 2025-11-28
> **Status**: Phase 3 Complete - All Core Features Migrated

## Overview

This document outlines the strategy and patterns for migrating from Next.js to React Router v7 in framework mode. We're taking an incremental approach by running both apps in parallel.

## Why React Router v7?

- **Faster development experience** - Hot module replacement is noticeably faster
- **Better file naming** - No more `page.tsx` everywhere, use descriptive names
- **Simpler mental model** - Less magic, more explicit
- **File-based routing** - Similar to Next.js but more flexible
- **Framework mode** - Full-stack capabilities like Next.js (SSR, loaders, actions)

## Project Structure

### Next.js (apps/web/)
```
apps/web/
├── app/
│   ├── chat-emilio/page.tsx
│   ├── test/page.tsx
│   └── ...
├── components/
│   ├── ui/              # Shadcn components
│   └── typography.tsx
└── lib/
```

### React Router (apps/web-react-router/)
```
apps/web-react-router/
├── app/
│   ├── routes/
│   │   ├── _index.tsx         # Index route (/)
│   │   ├── test.tsx           # /test route
│   │   ├── chat-emilio.tsx    # /chat-emilio route
│   │   └── ...                # Other routes auto-discovered
│   ├── routes.ts              # File-based routing config
│   ├── root.tsx               # Root layout with Clerk
│   └── app.css
├── .env                        # Vite env vars (VITE_ prefix)
└── react-router.config.ts      # React Router config
```

## Key Differences

### 1. File Naming & Routing

**Next.js:**
```
app/test/page.tsx              → /test
app/chat-emilio/page.tsx       → /chat-emilio
```

**React Router (File-based routing with @react-router/fs-routes):**
```
app/routes/_index.tsx          → /         (index route)
app/routes/test.tsx            → /test
app/routes/chat-emilio.tsx     → /chat-emilio
app/routes/about.tsx           → /about
app/routes/concerts.trending.tsx → /concerts/trending (dot = nested URL)
app/routes/concerts.$city.tsx  → /concerts/:city ($ = dynamic param)
```

File-based routing in `app/routes.ts`:
```typescript
import { type RouteConfig } from "@react-router/dev/routes";
import { flatRoutes } from "@react-router/fs-routes";

export default flatRoutes() satisfies RouteConfig;
```

**File naming conventions:**
- `_index.tsx` - Index route for parent directory
- `name.tsx` - Route at `/name`
- `parent.child.tsx` - Nested URL `/parent/child` (dots create segments)
- `$param.tsx` - Dynamic segment (`:param`)
- `_layout.tsx` - Pathless layout (wraps children without URL segment)
- `parent_.child.tsx` - Escape layout nesting (trailing underscore)
- `$.tsx` - Splat/catch-all route

### 2. Page Components

**Next.js:**
```typescript
"use client";  // Client components need this directive

export default function Page() {
  return <div>Content</div>;
}
```

**React Router:**
```typescript
import type { Route } from "./+types/test";

// No "use client" needed - components are client by default
// Use .server.tsx extension for server-only code

export function meta({}: Route.MetaArgs) {
  return [
    { title: "Test Page" },
    { name: "description", content: "Page description" },
  ];
}

export default function TestPage() {
  return <div>Content</div>;
}
```

### 3. Data Loading

**Next.js (Server Components):**
```typescript
export default async function Page() {
  const data = await fetch('...');
  return <div>{data}</div>;
}
```

**React Router (Loaders):**
```typescript
import type { Route } from "./+types/test";

export async function loader({ request }: Route.LoaderArgs) {
  const data = await fetch('...');
  return { data };
}

export default function TestPage({ loaderData }: Route.ComponentProps) {
  return <div>{loaderData.data}</div>;
}
```

### 4. Environment Variables

**Next.js:**
- Public: `NEXT_PUBLIC_*`
- Server: Any other name

**React Router (Vite):**
- Public: `VITE_*`
- Server: Any other name
- Access via `import.meta.env.VITE_*`

### 5. Authentication with Clerk

Both implementations are similar, but React Router uses middleware:

**Next.js:**
```typescript
// middleware.ts
import { clerkMiddleware } from '@clerk/nextjs/server';

export default clerkMiddleware();
```

**React Router:**
```typescript
// app/root.tsx
import { clerkMiddleware, rootAuthLoader } from '@clerk/react-router/server';

export const middleware: Route.MiddlewareFunction[] = [clerkMiddleware()];
export const loader = (args: Route.LoaderArgs) => rootAuthLoader(args);
```

Enable middleware in `react-router.config.ts`:
```typescript
export default {
  ssr: true,
  future: {
    v8_middleware: true,
  },
} satisfies Config;
```

## Migration Checklist

### Setup (✅ Complete)
- [x] Create new React Router app in `apps/web-react-router`
- [x] Install @clerk/react-router
- [x] Configure environment variables (.env with VITE_ prefix)
- [x] Set up Clerk authentication with middleware
- [x] Configure Tailwind CSS (included by default)
- [x] Test hello world and authentication
- [x] Migrate to file-based routing with @react-router/fs-routes

### Page Migration (✅ Phase 3 Complete)
- [x] Migrate test page as proof of concept
- [x] Create shared component library (lib/utils.ts with cn())
- [x] Migrate homepage (full portfolio content)
- [x] Configure path aliases (`~/` for `./app/`)
- [x] Migrate chat pages with FastAPI integration
- [x] Configure Vite proxy for /api/* routes to FastAPI (react-router.config.ts)
- [x] Migrate authenticated pages (scheduler with Clerk auth token)

### API Proxy Setup (✅ Complete)
All `/api/*` requests are proxied to FastAPI:
- **Dev**: `vite.config.ts` proxy → `http://127.0.0.1:8000`
- **Prod**: `server.js` Express proxy → `CUSTOM_RAILWAY_BACKEND_URL`

Just use `fetch('/api/...')` in your code - works everywhere.

### Component Migration (✅ Phase 3 Complete)
- [x] Create typography components (H1, H2, H3, P, Lead, Large, Small, Muted)
- [x] Port Button component from Shadcn UI
- [x] Install required dependencies (clsx, tailwind-merge, @radix-ui/react-slot, class-variance-authority)
- [x] Update import paths to use `~/` alias
- [x] Test component functionality
- [x] Weather component (for AI tool results display)
- [x] Mermaid component (for architecture diagrams)
- [x] MultiSelect component (for tenant messaging)
- [x] Scheduler component (web-only version for React Router)
- [x] Table components (for email responder)
- [x] Fixed Command component DialogProps import issue
- [x] DataTable component (for message-tenants with @tanstack/react-table)

### Feature Migration (✅ Phase 3 Complete)
- [x] Homepage
- [x] chat-emilio UI with FastAPI backend integration ✨
- [x] Navbar and sidebar
- [x] multi-agent-chat UI with Weather tool rendering
- [x] chat-weather UI
- [x] calendly - Schedule meeting (embedded Calendly widget)
- [x] ai-email-responder UI (main page + architecture subpage)
- [x] ai-email-responder-architecture with Mermaid diagram
- [x] Scheduler interface (with Clerk auth token via useAuth hook)
- [x] tenant-mass-messaging UI (with MultiSelect component)
- [x] message-tenants UI (with DataTable, row selection, filtering, sorting)
- [ ] Database queries (with Neon) - defer until needed

## Migration Patterns

### Pattern 1: Simple Page (Static Content)

**Before (Next.js):**
```typescript
// app/test/page.tsx
"use client";

import { H1, P } from "@/components/typography";

export default function Page() {
  return (
    <div className="p-4">
      <H1>Test Page</H1>
      <P>Content here</P>
    </div>
  );
}
```

**After (React Router):**
```typescript
// app/routes/test.tsx
import type { Route } from "./+types/test";

function H1({ children }: { children: React.ReactNode }) {
  return <h1 className="text-4xl font-extrabold">{children}</h1>;
}

export function meta({}: Route.MetaArgs) {
  return [
    { title: "Test Page" },
    { name: "description", content: "Description" },
  ];
}

export default function TestPage() {
  return (
    <div className="p-4">
      <H1>Test Page</H1>
      <p>Content here</p>
    </div>
  );
}
```

### Pattern 2: Protected Route

**Before (Next.js):**
```typescript
import { auth } from '@clerk/nextjs/server';
import { redirect } from 'next/navigation';

export default async function Page() {
  const { userId } = await auth();
  if (!userId) redirect('/sign-in');

  return <div>Protected content</div>;
}
```

**After (React Router):**
```typescript
import type { Route } from "./+types/protected";
import { redirect } from "react-router";
import { getAuth } from "@clerk/react-router/server";

export async function loader({ request }: Route.LoaderArgs) {
  const { userId } = await getAuth(request);
  if (!userId) throw redirect("/sign-in");

  return { userId };
}

export default function ProtectedPage({ loaderData }: Route.ComponentProps) {
  return <div>Protected content for {loaderData.userId}</div>;
}
```

### Pattern 3: Form with Server Action

**Before (Next.js):**
```typescript
"use server";

async function submitForm(formData: FormData) {
  const name = formData.get('name');
  // Process form
}

export default function Page() {
  return (
    <form action={submitForm}>
      <input name="name" />
      <button type="submit">Submit</button>
    </form>
  );
}
```

**After (React Router):**
```typescript
import type { Route } from "./+types/form";
import { Form, redirect } from "react-router";

export async function action({ request }: Route.ActionArgs) {
  const formData = await request.formData();
  const name = formData.get('name');
  // Process form

  return redirect("/success");
}

export default function FormPage() {
  return (
    <Form method="post">
      <input name="name" />
      <button type="submit">Submit</button>
    </Form>
  );
}
```

## Best Practices

### 1. Folder Structure
```
app/
├── routes/
│   ├── _protected/          # Nested routes with layout
│   │   ├── dashboard.tsx
│   │   └── settings.tsx
│   ├── api/                 # API-like routes (resource routes)
│   │   └── webhooks.tsx
│   └── public-page.tsx
├── components/
│   ├── ui/                  # Shadcn components
│   └── shared/              # Custom components
├── lib/
│   ├── utils.ts
│   └── api.ts
└── root.tsx                 # Root layout
```

### 2. Progressive Migration Strategy
1. Start with simple static pages
2. Move shared components early
3. Migrate authenticated pages
4. Convert API routes to loaders/actions
5. Test thoroughly at each step
6. Run both apps in parallel during transition

### 3. Component Sharing
Create a shared component library that works with both:
- Use Tailwind classes (framework agnostic)
- Avoid framework-specific APIs
- Use React 19 features (both support it)

### 4. Testing Strategy
- Keep existing Playwright tests for Next.js
- Create new tests for React Router
- Compare behavior between both
- Gradually replace old tests

## Dev Server Ports

- **Next.js**: http://localhost:3000
- **React Router**: http://localhost:5173
- **FastAPI**: http://localhost:8000

## Key Learnings

### What Works Well
- ✅ File-based routing is more intuitive (no more page.tsx)
- ✅ Faster HMR and development experience
- ✅ Clerk integration works seamlessly
- ✅ Tailwind CSS works out of the box
- ✅ TypeScript support is excellent with generated types

### Challenges
- ⚠️ Need to convert "use client" patterns to React Router patterns
- ⚠️ Environment variables need VITE_ prefix
- ⚠️ Import paths need updating (@/ may not work without configuration)
- ⚠️ Some Next.js specific APIs need alternatives

## Next Steps

1. **Set up shared component library**
   - Create `lib` folder with utilities
   - Port Shadcn UI components
   - Set up proper Tailwind configuration

2. **Migrate core pages**
   - Start with `/` homepage
   - `/chat-emilio`
   - `/multi-agent-chat`

3. **Set up API integration**
   - Configure Vercel AI SDK with React Router
   - Set up database connections
   - Port API endpoints to loaders/actions

4. **Testing**
   - Set up Playwright tests for React Router app
   - Create migration verification tests

## Resources

- [React Router v7 Docs](https://reactrouter.com/start/framework/installation)
- [Clerk React Router Docs](https://clerk.com/docs/react-router/getting-started/quickstart)
- [Vite Environment Variables](https://vite.dev/guide/env-and-mode.html)
- [Tailwind CSS v4](https://tailwindcss.com/docs)

## Questions & Decisions

### Q: Should we fully migrate or run both in parallel long-term?
**A**: TBD - Depends on migration complexity and feature parity

### Q: How to handle Shadcn UI components?
**A**: Copy to React Router app and update import paths

### Q: Database migrations?
**A**: Keep using same database, both apps can share it

### Q: API endpoints?
**A**: Use `fetch('/api/...')` - proxied to FastAPI via `vite.config.ts` (dev) and `server.js` (prod)
