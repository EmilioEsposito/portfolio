# Use a base Node image
FROM node:20-alpine AS base

# Install pnpm
RUN npm install -g pnpm

WORKDIR /app

# Copy workspace configs like pnpm-workspace.yaml, package.json, pnpm-lock.yaml
COPY pnpm-workspace.yaml ./
COPY package.json ./
COPY pnpm-lock.yaml ./

# Copy required shared packages (like features, ui)
RUN mkdir -p packages/ui
COPY packages/ui/package.json ./packages/ui/
COPY packages/ui/. ./packages/ui/
RUN mkdir -p packages/features
COPY packages/features/package.json ./packages/features/
COPY packages/features/. ./packages/features/

# Copy the Expo app's specific files
RUN mkdir -p apps/my-expo-app
COPY apps/my-expo-app/package.json ./apps/my-expo-app/
COPY apps/my-expo-app/. ./apps/my-expo-app/

# Install dependencies using pnpm workspace
RUN pnpm install --frozen-lockfile

# Expose the port Expo runs the web version on (default is 8081)
EXPOSE 8081

# Set the command to start the Expo development server for web
# Assumes the package name is 'my-expo-app'
CMD ["pnpm", "--filter", "my-expo-app", "web"] 