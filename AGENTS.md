This repo has a NextJS frontend and a FastAPI backend. It also has an Expo app but not really using it much, it is not a priority right now.

See README.md for more context.

See .vscode/launch.json for how to run the different services (again, we can ignore the Expo app for now).


You might need to run `touch .env.development.local` to create the file if it doesn't exist. However, you shouldn't need to have anything in this file, since it is meant for my local physical laptop. When you are running on cloud, you should have the same values injected into the environment variables when you deploy. Check if your env variables are being injected correctly by running `echo "testsecret length: length:${#MY_TEST_SECRET}"` in the terminal.

## Local PostgreSQL setup

The FastAPI app expects a PostgreSQL instance that provides the `portfolio` database and role. To recreate the local setup used in development:

1. Install PostgreSQL 16 locally (for example, `apt-get install postgresql-16`).
2. Start the PostgreSQL service and create a superuser role named `portfolio` with password `portfolio`:
   ```bash
   sudo -u postgres createuser --superuser portfolio
   sudo -u postgres psql -c "ALTER USER portfolio WITH PASSWORD 'portfolio';"
   ```
3. Create the `portfolio` database owned by that role:
   ```bash
   sudo -u postgres createdb -O portfolio portfolio
   ```
4. Verify connectivity with `psql "postgresql://portfolio:portfolio@localhost:5432/portfolio"`.

These credentials align with the SQLAlchemy job store configuration used by the FastAPI application's scheduler during local development.
