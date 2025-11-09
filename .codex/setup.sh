echo "Step 1: Installing pnpm dependencies"
pnpm install

echo "Step 2: Installing Playwright system dependencies"
echo "Installing browser runtime dependencies..."
sudo pnpm --filter web-nextjs exec playwright install-deps
echo "✓ Playwright system dependencies installed"

echo "Step 3: Installing Playwright browsers"
pnpm --filter web-nextjs exec playwright install
echo "✓ Playwright browsers installed"

echo "Step 4: Setting up Python environment"
uv venv
source .venv/bin/activate
uv sync -p python3.11

echo "Step 5: Creating .env file"
touch .env.development.local
# NOTE TO AGENTS: This .env.development.local file is not used by codex, it is only used for Emilio's local laptop development. 
echo "✓ Created .env.development.local"

echo "Step 6: Configuring PYTHONPATH"
echo 'export PYTHONPATH=/workspace/portfolio:$PYTHONPATH' >> ~/.bashrc
export PYTHONPATH=/workspace/portfolio:$PYTHONPATH
echo "✓ PYTHONPATH set to: $PYTHONPATH"

echo "Step 7: Installing and starting PostgreSQL"
echo "Installing PostgreSQL 16..."
sudo apt-get update -qq
sudo apt-get install -y -qq postgresql-16 postgresql-contrib-16
echo "✓ PostgreSQL 16 installed"

echo "Starting PostgreSQL service..."
sudo service postgresql start
sleep 2
echo "✓ PostgreSQL service started"

echo "Creating portfolio user..."
sudo -u postgres psql -c "CREATE USER portfolio WITH PASSWORD 'portfolio' SUPERUSER;" 2>&1 && echo "✓ User created" || echo "⚠ User may already exist"

echo "Creating portfolio database..."
sudo -u postgres psql -c "CREATE DATABASE portfolio OWNER portfolio;" 2>&1 && echo "✓ Database created" || echo "⚠ Database may already exist"

echo "Step 8: Running database migrations"
uv run alembic upgrade head

echo "✓ Setup complete!"
