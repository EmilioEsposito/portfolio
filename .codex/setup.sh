echo "Step 1: Installing pnpm dependencies"
pnpm install

echo "Step 2: Setting up Python environment"
uv venv
source .venv/bin/activate
uv sync -p python3.11

echo "Step 3: Creating .env file"
touch .env.development.local
echo "✓ Created .env.development.local"

echo "Step 4: Configuring PYTHONPATH"
echo 'export PYTHONPATH=/workspace/portfolio:$PYTHONPATH' >> ~/.bashrc
export PYTHONPATH=/workspace/portfolio:$PYTHONPATH
echo "✓ PYTHONPATH set to: $PYTHONPATH"

echo "Step 5: Setting up PostgreSQL"
echo "Creating portfolio user..."
psql -U postgres -h localhost -c "CREATE USER portfolio WITH PASSWORD 'portfolio' SUPERUSER;" 2>&1 && echo "✓ User created" || echo "⚠ User may already exist"
echo "Creating portfolio database..."
psql -U postgres -h localhost -c "CREATE DATABASE portfolio OWNER portfolio;" 2>&1 && echo "✓ Database created" || echo "⚠ Database may already exist"


echo "Step 6: Running database migrations"
uv run alembic upgrade head

echo "✓ Setup complete!"
