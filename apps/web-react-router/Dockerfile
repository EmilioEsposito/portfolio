# Build context is repo root (not apps/web-react-router)
# Uses workspace lockfile for single source of truth

FROM node:22-alpine AS base
RUN corepack enable
WORKDIR /app

FROM base AS deps
COPY pnpm-lock.yaml pnpm-workspace.yaml package.json .npmrc ./
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
# Copy root package.json for pnpm config, .npmrc for build settings, app package.json for deps
COPY package.json pnpm-lock.yaml .npmrc ./
COPY apps/web-react-router/package.json ./apps/web-react-router/
WORKDIR /app/apps/web-react-router
RUN pnpm install --prod --ignore-scripts
COPY --from=build /app/apps/web-react-router/build ./build
COPY apps/web-react-router/server.js ./server.js
ENV PORT=5173
EXPOSE 5173
CMD ["node", "server.js"]
