# Base image for building the frontend
FROM node:20-alpine AS builder

# Install pnpm
RUN npm install -g pnpm

WORKDIR /app

# Copy dependency definition files
COPY package.json pnpm-lock.yaml ./

# Install dependencies
RUN pnpm install --frozen-lockfile

# Copy the rest of the application code
COPY . .

# Explicitly set the Docker Compose flag for the build environment.
ENV DOCKER_ENV=true

# Build the Next.js application
RUN echo ">>> Attempting pnpm build..."

# Prioritize ENV VARS, fallback to sourcing optional secret file (should only be used locally), then build.
RUN --mount=type=secret,id=dotenv,required=false \
    # Check if secret file exists and source it if it does (this should only be used locally)
    if [ -f /run/secrets/dotenv ]; then \
      echo ">>> Sourcing /run/secrets/dotenv with set -a..." && \
      set -a && . /run/secrets/dotenv && set +a; \
    else \
      echo ">>> /run/secrets/dotenv not found, relying on pre-existing environment variables."; \
    fi && \
    \
    # Final check & export: ensure required variables are set (DB URL, Clerk Key)
    echo ">>> Final check: DATABASE_URL starts with: $(echo $DATABASE_URL | cut -c 1-10)..." && \
    if [ -z "$DATABASE_URL" ]; then echo '>>> ERROR: DATABASE_URL is missing!'; exit 1; fi && \
    export DATABASE_URL && \
    \
    echo ">>> Final check: NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY starts with: $(echo $NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY | cut -c 1-10)..." && \
    if [ -z "$NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY" ]; then echo '>>> ERROR: NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY is missing!'; exit 1; fi && \
    export NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY && \
    \
    echo ">>> Running pnpm build..." && \
    pnpm build

RUN echo ">>> pnpm build finished."

# Prune development dependencies
RUN pnpm prune --prod

# --- Runner Stage ---
FROM node:20-alpine AS runner

# Install pnpm globally in the runner stage as well
RUN npm install -g pnpm

WORKDIR /app

# Copy necessary files from the builder stage
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/package.json ./
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/public ./public

# Expose the port the app runs on
EXPOSE 3000

# Command to run the application
CMD ["pnpm", "start"] 