# Base image for the fastapi
FROM python:3.11-slim

RUN echo ">>> Building BACKEND..."

WORKDIR /app/api

# Copy the api directory contents into the working directory /app/api
COPY api/ .

ENV PYTHONPATH=/app

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Expose the port the API runs on (default 8000)
# Note: Railway might assign a different port via the PORT env var.
EXPOSE 8000

# Command to run the Hypercorn server
# It will find index:app in the /app directory
# Uses the PORT environment variable if set by the platform (e.g., Railway), otherwise defaults to 8000.
# Binds to IPv6 ([::]) as required for Railway Private Networking.
CMD ["sh", "-c", "hypercorn index:app --bind [::]:${PORT:-8000}"] 