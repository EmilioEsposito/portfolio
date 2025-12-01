# Portfolio

A monorepo for personal learning projects and production AI-based services for my rental real estate business, Sernia Capital LLC.


## Tech Stack:
* Frontend:
    * Web:
        * Framework: React Router v7 (framework mode)
        * UI: [Shadcn UI](https://ui.shadcn.com/docs)
        * Styling: Tailwind CSS
    * Mobile:
        * Framework: Expo
        * UI: TBD - [Expo UI (Beta)](https://docs.expo.dev/versions/v53.0.0/sdk/ui/)? [React Native Reusables](https://rnr-docs.vercel.app/getting-started/introduction/)? Want something that can render on ios, android, and web.
    * Package Manager: pnpm
* Backend: [FastAPI](https://fastapi.tiangolo.com/)
    * Database: [Neon Postgres](https://neon.tech/)
    * Package Manager: uv
* Deployment: [Railway](https://railway.com/project/73eb837a-ba86-4899-992c-cefd0c22b91f?environmentId=455c3498-682b-4e4d-9e1f-4c13c3e9eb59)


## Setup

0. Clone the repository `git clone https://github.com/EmilioEsposito/portfolio.git`
1. Sign up for accounts with the AI providers you want to use (e.g., OpenAI, Anthropic).
2. Obtain API keys for each provider.
3. Set the required environment variables as shown in the `.env.example` file, but in a new file called `.env`.
4. Install [pnpm](https://pnpm.io/installation) if you don't have it.
5. Install [uv](https://github.com/astral-sh/uv#installation) if you don't have it.
6. `pnpm install` to install the required Node dependencies.
7. `uv venv` to create a virtual environment named `.venv`.
8. `source .venv/bin/activate` (or `\.venv\Scripts\activate` on Windows) to activate the virtual environment.
9. `uv sync -p python3.11` to install Python dependencies from `pyproject.toml` (using Python 3.11).
10. `uv sync --dev -p python3.11` to install optional dev dependencies.
11. `pnpm dev` to launch the development server (or see other scripts in `package.json`).
12. Install and use [Railway CLI](https://docs.railway.com/guides/cli)

## Docker Setup

These instructions assume you have Docker and Docker Compose installed (e.g., via Docker Desktop).

1. The [docker-compose.yml](docker-compose.yml) is just meant for local builds. Ensure `.env` file exists for local development.  The file will use this file to populate the environment variables for the containers.

### Local Postgres database

Neon is not reachable from the Codespaces-style environment used for these tasks. You can run a local Postgres instance with Docker and point both FastAPI and Next.js at it instead:

1. Copy `.env.example` to `.env` (or update your existing file) and uncomment the variables in the `Local Postgres (docker compose)` section. Set `DATABASE_REQUIRE_SSL=false` so the API disables SSL requirements when talking to the local database.
2. (Optional) Adjust the `POSTGRES_*` values if you want a different database name, user, or password. Make sure the `DATABASE_URL` and `DATABASE_URL_UNPOOLED` strings match.
3. Start the database container:

   ```bash
   docker compose --env-file .env up -d postgres
   ```

4. Apply the migrations so the schema exists locally:

   ```bash
   cd api
   uv run alembic upgrade head
   ```

   (If `uv` is not installed you can also run `sh api/db_run_migration.sh` from the root of the project.)
5. Launch the other services (FastAPI, React Router, etc.) once the database is ready. Both apps will reuse the same environment variables.

2. **Build and Run:**
   ```bash
   docker compose --env-file .env build --no-cache | tee docker_build.log
   docker compose --env-file .env up -d | tee docker_up.log
   ```

   Frontend React Router only:

   ```bash
   docker compose --env-file .env build web-react-router --no-cache  | tee docker_build.log
   docker compose --env-file .env up -d web-react-router | tee docker_up.log
   ```

   Backend FastAPI only:

   ```bash
   docker compose --env-file .env build fastapi --no-cache | tee docker_build.log
   docker compose --env-file .env up -d fastapi | tee docker_up.log
   ```

   Expo App only:

   ```bash
   docker compose --env-file .env build my-expo-app  | tee docker_build.log
   docker compose --env-file .env up -d my-expo-app | tee docker_up.log
   ```

    Or all in one with cache:
    ```bash
    docker compose --env-file .env up -d --build | tee docker_up_build.log
    ```

   This command builds the Docker images for the web-react-router and fastapi services (if they don't exist or have changed) and starts the containers.

3. **Accessing the Application:**
   - Frontend React Router: [http://localhost:5173](http://localhost:5173)
   - Backend FastAPI: [http://localhost:8000/api/docs](http://localhost:8000/api/docs)

4. **Stopping the Application:**
   Press `Ctrl+C` in the terminal where `docker compose up` is running.
   To remove the containers (optional):
   ```bash
   docker compose down
   ```

## Environments:

* Localhost:
    * React Router: http://localhost:5173
    * FastAPI (via React Router proxy): http://localhost:5173/api/docs
    * FastAPI (direct): http://localhost:8000/api/docs
* Dev:
    * React Router: https://dev.eesposito.com
    * FastAPI (via React Router proxy): https://dev.eesposito.com/api/docs
    * FastAPI (direct): https://dev-eesposito-fastapi.up.railway.app/api/docs
* Production:
    * React Router: https://eesposito.com
    * FastAPI (via React Router proxy): https://eesposito.com/api/docs
    * FastAPI (direct): https://eesposito-fastapi.up.railway.app/api/docs


## Environment Variables Management

This project uses Railway for deployment and environment variable management.

Locally, the file that takes all precedence is `.env`. 

TODO: Document Railway CLI commands for env variables so AI can help

Expo-go will need the local IP address of the machine running the backend. Run this to update the environment variable in .env:
```bash
# Update the CUSTOM_RAILWAY_BACKEND_URL environment variable in .env
# This command checks if the variable exists, and if so, updates it with the current IP address
# Otherwise, it adds the variable to the file
grep -q '^CUSTOM_RAILWAY_BACKEND_URL=' .env \
  && sed -i '' "s|^CUSTOM_RAILWAY_BACKEND_URL=.*|CUSTOM_RAILWAY_BACKEND_URL=http://$(ipconfig getifaddr en0):8000|" .env \
  || echo "CUSTOM_RAILWAY_BACKEND_URL=http://$(ipconfig getifaddr en0):8000" >> .env
```


## 3rd Party Stuff

OpenPhone and Google PubSub webhooks are pointing to the production environment. Use `ngrok http 8000` to test the webhooks locally. 


# Deprecation Candidates

* Google PubSub (replace with Oauth)
* app/components/google/account-switcher.tsx (replace with Clerk)
* Native Google Oauth handling - replace with Clerk Oauth
    * app/auth


# Cursor Background Agent 

```bash
docker build -t cursor-agent-test -f .cursor/Dockerfile .cursor
```

```bash
docker run --name cursor-agent-interactive-test --rm -it -v "$(pwd):/app" cursor-agent-test:latest /bin/bash
```




noop-test-20251110