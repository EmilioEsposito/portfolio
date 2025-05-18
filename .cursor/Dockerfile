# .cursor/Dockerfile
FROM ubuntu:22.04

RUN echo ">>> Building Emilio's Cursor Agent Docker image..."

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

# Set up the working directory
WORKDIR /workspace



# Set VIRTUAL_ENV so uv targets it automatically when .venv is created by install.sh
# This also helps if commands are run not from the root of the project within the agent.
ENV VIRTUAL_ENV=/workspace/.venv
# Add the venv's bin directory to the PATH
ENV PATH="/workspace/.venv/bin:$PATH"

# The CMD/ENTRYPOINT are not strictly necessary here as Cursor will run commands from environment.json
# CMD ["/bin/bash"]
