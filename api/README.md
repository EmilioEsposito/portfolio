# FastAPI backend

The API serves public AI demos and authenticated Sernia operations. `index.py`
mounts routers and owns the application startup lifecycle; feature modules live
under `src/`. Feature modules own their SQLAlchemy models; shared settings and Alembic migrations
live in `src/database/`.

From the repository root, after [development setup](../docs/DEVELOPMENT.md):

```bash
pnpm fastapi-dev
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```

The launcher resolves API configuration without printing credentials. API docs
are available at `http://localhost:8000/api/docs`. Tests live in `src/tests/`;
live provider tests are excluded by default.

Start with the guides for [public demos](src/ai_demos/README.md),
[Sernia AI](src/sernia_ai/README.md), [Zillow Leads Agent](src/zillow_leads/README.md),
[rental listings](src/rental_listings/README.md), [SMS](src/open_phone/README.md),
[Google](src/google/README.md), and [schedulers](src/schedulers/README.md).
Production builds use [Dockerfile](Dockerfile) and
[railway_fastapi.json](railway_fastapi.json).
