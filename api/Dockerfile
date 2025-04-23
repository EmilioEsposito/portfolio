# Base image for the backend
FROM python:3.11-slim

WORKDIR /app

# Copy requirements file from the root context
COPY requirements.txt requirements.txt

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the api directory contents into the working directory /app
COPY api/ .

# Copy the api_src directory into the working directory /app
COPY api_src/ ./api_src

# Expose the port the API runs on (default 8000)
EXPOSE 8000

# Command to run the Uvicorn server
# It will find index:app in the /app directory
# Imports from api_src should work as it's now in /app/api_src
CMD ["uvicorn", "index:app", "--host", "0.0.0.0", "--port", "8000"] 