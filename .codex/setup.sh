pnpm install
uv venv
source .venv/bin/activate
uv sync -p python3.11

# create .env.development.local file (even though it should not be used by codex)
touch .env.development.local

# create postgres user and local development database (creds not sensitive since this is sandboxed env, same as local laptop)
sudo -u postgres createuser --superuser portfolio
sudo -u postgres psql -c "ALTER USER portfolio WITH PASSWORD 'portfolio';"
sudo -u postgres createdb -O portfolio portfolio

# apply migrations
uv run alembic upgrade head

