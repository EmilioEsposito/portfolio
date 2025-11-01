# Base image for building the nextjs
FROM node:20-alpine AS builder

# Declare build-time arguments
ARG DATABASE_URL
ARG NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY
ARG CUSTOM_RAILWAY_BACKEND_URL


# # Set environment variables available during the build
# ENV DATABASE_URL=${DATABASE_URL}
# ENV NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=${NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY}
# # Expose Railway env name during build
# ENV CUSTOM_RAILWAY_BACKEND_URL=${CUSTOM_RAILWAY_BACKEND_URL}

RUN echo ">>> Building FRONTEND..."
RUN echo "$(date)"
RUN echo ">>> CUSTOM_RAILWAY_BACKEND_URL=${CUSTOM_RAILWAY_BACKEND_URL}"

# Install pnpm (pinned to prevent breaking changes)
RUN npm install -g pnpm@10.20.0

WORKDIR /app

# Copy workspace configuration first
COPY pnpm-workspace.yaml ./
COPY package.json ./
COPY pnpm-lock.yaml ./

# Copy the shared packages required by the web app
RUN mkdir -p packages/ui
COPY packages/ui/package.json ./packages/ui/
COPY packages/ui/. ./packages/ui/
RUN mkdir -p packages/features
COPY packages/features/package.json ./packages/features/
COPY packages/features/. ./packages/features/

# Copy the web app's source code and package.json
# Create the directory structure first
RUN mkdir -p apps/web
COPY apps/web/package.json ./apps/web/
COPY apps/web/. ./apps/web/


# Install dependencies using workspace protocol
RUN echo ">>> Installing dependencies..."
RUN pnpm install --frozen-lockfile

# Explicitly set the Docker Compose flag for the build environment.
ENV DOCKER_ENV=true

# fail if any of these are missing
RUN if [ -z "$DOCKER_ENV" ]; then echo '>>> ERROR: DOCKER_ENV is missing!'; exit 1; fi
RUN if [ -z "$DATABASE_URL" ]; then echo '>>> ERROR: DATABASE_URL is missing!'; exit 1; fi
RUN if [ -z "$NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY" ]; then echo '>>> ERROR: NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY is missing!'; exit 1; fi
# We don't fail if CUSTOM_RAILWAY_BACKEND_URL is missing, as it's only present in Railway


# Build the Next.js application using workspace command
RUN echo ">>> Attempting pnpm build..."
# Use pnpm filter to build the specific app
RUN pnpm --filter my-nextjs-app build
RUN echo ">>> pnpm build finished."

# Skip pruning for now to avoid breaking workspace structure
# TODO: Investigate proper pruning for monorepo setup
# RUN echo ">>> Pruning dev dependencies..."
# ENV CI=true
# RUN pnpm prune --prod

# --- Runner Stage ---
FROM node:20-alpine AS runner

# Install pnpm globally in the runner stage as well (pinned to prevent breaking changes)
RUN npm install -g pnpm@10.20.0

# Set working directory for the runner
WORKDIR /app

# Copy necessary workspace files from the builder stage
COPY --from=builder /app/package.json ./
COPY --from=builder /app/pnpm-workspace.yaml ./
COPY --from=builder /app/pnpm-lock.yaml ./

# Copy the pruned node_modules from the root of the builder stage
COPY --from=builder /app/node_modules ./node_modules

# Copy workspace packages (needed for workspace dependencies)
COPY --from=builder /app/packages ./packages

# Copy the entire pruned web app directory from the builder stage
# This includes .next, package.json, public, etc.
COPY --from=builder /app/apps/web ./apps/web

# Expose the port the app runs on
EXPOSE 3000

# Set the default command to run the start script from within the web app directory
CMD ["pnpm", "--filter", "my-nextjs-app", "start"]