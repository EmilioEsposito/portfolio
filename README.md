# Portfolio

This is my portfolio/sandbox website that I use to just play around with new technologies. 


## Tech Stack:
* Frontend: 
    * Web: 
        * Framework: Next.js
        * UI: [Shadcn UI](https://ui.shadcn.com/docs)
        * Styling: Tailwind CSS
    * Mobile: 
        * Framework: Expo
        * UI: TBD - [Expo UI (Beta)](https://docs.expo.dev/versions/v53.0.0/sdk/ui/)? [React Native Reusables](https://rnr-docs.vercel.app/getting-started/introduction/)? Want something that can render on ios, android, and web.
* Backend: [FastAPI](https://fastapi.tiangolo.com/)
    * Database: [Neon Postgres](https://neon.tech/)
* Deployment: [Railway](https://railway.com/project/73eb837a-ba86-4899-992c-cefd0c22b91f?environmentId=455c3498-682b-4e4d-9e1f-4c13c3e9eb59)
* Package Managers: pnpm and pip

## Setup

0. Clone the repository `git clone https://github.com/EmilioEsposito/portfolio.git`
1. Sign up for accounts with the AI providers you want to use (e.g., OpenAI, Anthropic).
2. Obtain API keys for each provider.
3. Set the required environment variables as shown in the `.env.example` file, but in a new file called `.env`.
4. `pnpm install` to install the required Node dependencies.
5. `virtualenv venv` to create a virtual environment.
6. `source venv/bin/activate` to activate the virtual environment.
7. `pip install -r requirements.txt` to install the required Python dependencies.
8. `pnpm dev` to launch the development server.
9. Install and use [Railway CLI](https://docs.railway.com/guides/cli)

## Docker Setup

These instructions assume you have Docker and Docker Compose installed (e.g., via Docker Desktop).

1. The [docker-compose.yml](docker-compose.yml) is just meant for local builds. Ensure `.env.development.local` file exists for local development.  The file will use this file to populate the environment variables for the containers.

2. **Build and Run:**
   ```bash
   docker compose --env-file .env.development.local build --no-cache | tee docker_build.log       
   docker compose --env-file .env.development.local up -d | tee docker_up.log       
   ```

   Frontend NextJS only:

   ```bash
   docker compose --env-file .env.development.local build nextjs --no-cache  | tee docker_build.log        
   docker compose --env-file .env.development.local up -d nextjs | tee docker_up.log       
   ```

   Backend FastAPI only:

   ```bash
   docker compose --env-file .env.development.local build fastapi  | tee docker_build.log        
   docker compose --env-file .env.development.local up -d fastapi | tee docker_up.log       
   ```

    Or all in one with cache:
    ```bash
    docker compose --env-file .env.development.local up -d --build | tee docker_up_build.log       
    ```

   This command builds the Docker images for the nextjs and fastapi services (if they don't exist or have changed) and starts the containers.

3. **Accessing the Application:**
   - Frontend NextJS: [http://localhost:3000](http://localhost:3000)
   - Backend FastAPI: [http://localhost:8000/api/docs](http://localhost:8000/api/docs)

4. **Stopping the Application:**
   Press `Ctrl+C` in the terminal where `docker compose up` is running.
   To remove the containers (optional):
   ```bash
   docker compose down
   ```

## Environments:

* Localhost: 
    * NextJS: http://localhost:3000
    * FastAPI (via NextJS proxy): http://localhost:3000/api/docs
    * FastAPI (direct): http://localhost:8000/api/docs
* Dev: 
    * NextJS: https://dev.eesposito.com
    * FastAPI (via NextJS proxy): https://dev.eesposito.com/api/docs
    * FastAPI (direct): https://dev-eesposito-fastapi.up.railway.app/api/docs
* Production: 
    * NextJS: https://eesposito.com
    * FastAPI (via NextJS proxy): https://eesposito.com/api/docs
    * FastAPI (direct): https://eesposito-fastapi.up.railway.app/api/docs


## Environment Variables Management

This project uses Railway for deployment and environment variable management.

Locally, the file that takes all precedence is `.env.development.local`. 

TODO: Document Railway CLI commands for env variables so AI can help


## 3rd Party Stuff

OpenPhone and Google PubSub webhooks are pointing to the dev environment for now. Need to setup ngrok or something to test the webhooks locally. 


# Deprecation Candidates

* Google PubSub (replace with Oauth)
* app/components/google/account-switcher.tsx (replace with Clerk)
* Native Google Oauth handling - replace with Clerk Oauth
    * app/auth


