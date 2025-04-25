# Portfolio

This is my portfolio/sandbox website that I use to just play around with new technologies. 


## Tech Stack:
* Frontend: Next.js
    * UI: [Shadcn UI](https://ui.shadcn.com/docs) & Tailwind CSS
* Backend: [FastAPI](https://fastapi.tiangolo.com/)
    * Database: [Neon Postgres](https://neon.tech/)
* Deployment: [Vercel](https://vercel.com/) (FastAPI is deployed as a Serverless Function)
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
9. Install and use Vercel CLI, which can help test Serverless functions.
    ```bash
    pnpm install vercel
    pnpm vercel login
    pnpm vercel dev
    ```

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
    * FastAPI: http://localhost:8000/api/docs (also at http://localhost:3000/api/docs)
* Dev: 
    * NextJS: https://dev.eesposito.com
    * FastAPI: https://dev.eesposito.com/api/docs
* Production: 
    * NextJS: https://eesposito.com
    * FastAPI: https://eesposito.com/api/docs

[All Vercel Deployments](https://vercel.com/emilioespositos-projects/portfolio/deployments)


## Environment Variables Management

This project uses Vercel for deployment and environment variable management.

Locally, the file that takes all precedence is `.env.development.local`. 

### Local Development Setup

```bash
vercel env pull .env.development.local
```

### Other Vercel env management commands
```bash
vercel env pull .env.development.local    # Pulls Development environment vars
```

```bash
vercel env pull .env.production.local     # Pulls Production environment vars
```

```bash
vercel env pull .env.preview.local        # Pulls Preview environment vars
```

# Add a new environment variable
vercel env add MY_VAR                     # Interactive prompt will ask for value and environment

# List all environment variables
vercel env ls

# Remove an environment variable
vercel env rm MY_VAR

# Add with specific environment target
vercel env add MY_VAR production         # Specifically add to production
vercel env add MY_VAR development        # Specifically add to development
vercel env add MY_VAR preview            # Specifically add to preview
```

## 3rd Party Dev

I use a `x-vercel-protection-bypass` param in external services like OpenPhone and Google PubSub in order to allow testing on preview deployments. 
This allows those webhooks to to my preview routes, bypassing Vercel protection (which is fine since I verify the calls anyways). 

[Vercel Deployment Protection](https://vercel.com/emilioespositos-projects/portfolio/settings/deployment-protection)

OpenPhone has a prod and dev webhook, each with its own secret. 

For now, on Google PubSub I'm using just using the dev endpoint since the product is not production ready yet. 


# Deprecation Candidates

* Google PubSub (replace with Oauth)
* app/components/google/account-switcher.tsx (replace with Clerk)
* Native Google Oauth handling - replace with Clerk Oauth
    * app/auth

