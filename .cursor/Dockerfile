# .cursor/Dockerfile
# This Dockerfile defines the BASE ENVIRONMENT for Cursor cloud agents.
# It installs system-level tools that get baked into a snapshot.
# The snapshot is then reused by all agents for fast startup.

FROM ubuntu:22.04

RUN echo ">>> Building Cursor cloud agent base environment..."

# Avoid prompts from apt
ENV DEBIAN_FRONTEND=noninteractive

# =====================================
# Basic System Utilities
# =====================================
RUN apt-get update && apt-get install -y \
    curl \
    git \
    sudo \
    gnupg \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# =====================================
# Node.js 20.x & pnpm
# =====================================
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - \
    && apt-get install -y nodejs \
    && npm install -g pnpm \
    && rm -rf /var/lib/apt/lists/*

# =====================================
# Python 3.11
# =====================================
RUN apt-get update && apt-get install -y \
    python3.11 \
    python3.11-dev \
    python3.11-venv \
    python3-pip \
    build-essential \
    && update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1 \
    && python3 -m pip install --upgrade pip \
    && rm -rf /var/lib/apt/lists/*

# =====================================
# uv (Python package manager)
# =====================================
# Copy uv binary from the official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/
RUN chmod +x /usr/local/bin/uv /usr/local/bin/uvx

# =====================================
# PostgreSQL 16
# =====================================
# Install PostgreSQL in the base image so it's available immediately
RUN apt-get update && apt-get install -y \
    postgresql-16 \
    postgresql-contrib-16 \
    && rm -rf /var/lib/apt/lists/*

# =====================================
# Playwright System Dependencies
# =====================================
# Install system libraries required by Playwright browsers
# These are large and rarely change, so bake them into the snapshot
RUN apt-get update && apt-get install -y \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    libatspi2.0-0 \
    libxshmfence1 \
    && rm -rf /var/lib/apt/lists/*

# =====================================
# Workspace Setup
# =====================================
WORKDIR /home/ubuntu

# Create the Python virtual environment at build time
# This venv will exist in the snapshot and be reused by all agents
RUN python3 -m venv /home/ubuntu/.venv

# Set VIRTUAL_ENV so uv and Python commands automatically use this venv
ENV VIRTUAL_ENV=/home/ubuntu/.venv
ENV PATH="/home/ubuntu/.venv/bin:$PATH"

# Ensure proper ownership of home directory
RUN chown -R ubuntu:ubuntu /home/ubuntu

# =====================================
# Playwright Browsers (Pre-install)
# =====================================
# Note: We install a generic set of Playwright browsers here for faster agent startup.
# The specific version will be updated by install.sh to match your project's package.json.
# Install latest Playwright globally to get browsers
RUN npm install -g playwright@latest \
    && playwright install chromium firefox webkit \
    && rm -rf /root/.cache

# =====================================
# User Setup
# =====================================
# The 'ubuntu' user should already exist in ubuntu:22.04
# Ensure it has sudo access without password for PostgreSQL management
RUN usermod -aG sudo ubuntu \
    && echo "ubuntu ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers.d/ubuntu

USER ubuntu

# Set Playwright browser path for the ubuntu user
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

RUN echo ">>> Base environment build complete!"

