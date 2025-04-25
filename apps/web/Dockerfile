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

# Install pnpm
RUN npm install -g pnpm

WORKDIR /app

# Copy dependency definition files
COPY pnpm-lock.yaml ./
COPY apps/web/package.json ./

# Install dependencies
RUN pnpm install --no-frozen-lockfile

# Copy the rest of the application code for the web app
COPY apps/web/. ./apps/web/

# Explicitly set the Docker Compose flag for the build environment.
ENV DOCKER_ENV=true

# fail if any of these are missing
RUN if [ -z "$DOCKER_ENV" ]; then echo '>>> ERROR: DOCKER_ENV is missing!'; exit 1; fi
RUN if [ -z "$DATABASE_URL" ]; then echo '>>> ERROR: DATABASE_URL is missing!'; exit 1; fi
RUN if [ -z "$NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY" ]; then echo '>>> ERROR: NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY is missing!'; exit 1; fi
# We don't fail if CUSTOM_RAILWAY_BACKEND_URL is missing, as it's only present in Railway

# Build the Next.js application
RUN echo ">>> Attempting pnpm build..."

RUN cd apps/web && pnpm build

RUN echo ">>> pnpm build finished."

# Prune development dependencies
RUN cd apps/web && pnpm prune --prod

# --- Runner Stage ---
FROM node:20-alpine AS runner

# Install pnpm globally in the runner stage as well
RUN npm install -g pnpm

# Set working directory for the runner
WORKDIR /app

# Copy necessary files from the builder stage
COPY --from=builder /app/apps/web/.next ./.next
COPY --from=builder /app/apps/web/package.json ./package.json
COPY --from=builder /app/apps/web/node_modules ./node_modules
COPY --from=builder /app/apps/web/public ./public
COPY --from=builder /app/pnpm-lock.yaml ./

# Expose the port the app runs on
EXPOSE 3000

# Command to run the application
CMD ["pnpm", "start"] 