# Build context is repo root (not apps/web-react-router)
# Uses workspace lockfile for single source of truth

FROM node:20-alpine AS base
RUN corepack enable && corepack prepare pnpm@latest --activate
WORKDIR /app

FROM base AS deps
COPY pnpm-lock.yaml pnpm-workspace.yaml package.json ./
COPY apps/web-react-router/package.json ./apps/web-react-router/
RUN pnpm install --filter web-react-router

FROM base AS build
COPY --from=deps /app/node_modules ./node_modules
COPY --from=deps /app/apps/web-react-router/node_modules ./apps/web-react-router/node_modules
COPY apps/web-react-router ./apps/web-react-router
WORKDIR /app/apps/web-react-router
RUN pnpm run build

FROM base
WORKDIR /app
COPY apps/web-react-router/package.json pnpm-lock.yaml ./
RUN pnpm install --prod
COPY --from=build /app/apps/web-react-router/build ./build
COPY apps/web-react-router/server.js ./server.js
ENV PORT=5173
EXPOSE 5173
CMD ["node", "server.js"]
