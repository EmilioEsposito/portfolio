# Base image for building the frontend
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

# fail if any of these are missing
RUN if [ -z "$DOCKER_ENV" ]; then echo '>>> ERROR: DOCKER_ENV is missing!'; exit 1; fi
RUN if [ -z "$DATABASE_URL" ]; then echo '>>> ERROR: DATABASE_URL is missing!'; exit 1; fi
RUN if [ -z "$NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY" ]; then echo '>>> ERROR: NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY is missing!'; exit 1; fi
# We don't fail if CUSTOM_RAILWAY_BACKEND_URL is missing, as it's only present in Railway

# Build the Next.js application
RUN echo ">>> Attempting pnpm build..."

RUN pnpm build

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