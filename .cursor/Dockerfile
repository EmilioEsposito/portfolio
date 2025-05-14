# .cursor/Dockerfile
FROM ubuntu:22.04

# Avoid prompts from apt
ENV DEBIAN_FRONTEND=noninteractive

# Install basic utilities
RUN apt-get update && apt-get install -y \
    curl \
    git \
    sudo \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js 20.x and pnpm
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - \
    && sudo apt-get install -y nodejs \
    && sudo npm install -g pnpm

# Install Python 3.11
RUN sudo apt-get update && sudo apt-get install -y \
    python3.11 \
    python3.11-dev \
    python3.11-venv \
    python3-pip \
    && sudo update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1 \
    && sudo rm -rf /var/lib/apt/lists/*
RUN python3 -m pip install --upgrade pip

# Install uv
# Copy uv binary from the official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/
# Ensure uv is executable
RUN chmod +x /usr/local/bin/uv /usr/local/bin/uvx

# Create a non-root user and group
RUN groupadd -r agentgroup && useradd -r -g agentgroup -ms /bin/bash -d /home/agentuser agentuser
# Allow agentuser to run sudo commands without a password (optional, but can be useful for dev env setup)
RUN echo "agentuser ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers

# Set up the working directory
WORKDIR /app
# Change ownership of /app to agentuser *before* switching user, if needed for subsequent Dockerfile steps
# For agent cloning repo into /app, permissions will be handled by agent runtime
RUN sudo chown agentuser:agentgroup /app

# Switch to the non-root user
USER agentuser

# Set VIRTUAL_ENV so uv targets it automatically when .venv is created by install.sh
# This also helps if commands are run not from the root of the project within the agent.
ENV VIRTUAL_ENV=/app/.venv
# Add the venv's bin directory to the PATH
ENV PATH="/app/.venv/bin:$PATH"

# The CMD/ENTRYPOINT are not strictly necessary here as Cursor will run commands from environment.json
# CMD ["/bin/bash"]
