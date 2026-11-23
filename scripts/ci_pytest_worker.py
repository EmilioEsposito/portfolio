"""Route CI xdist workers to their own pre-seeded Postgres databases."""

import os

# Explicit CLI plug-in, loaded before conftests and application imports.
if worker := os.environ.get("PYTEST_XDIST_WORKER"):
    database_url = "postgresql://portfolio:portfolio@localhost:5432/portfolio"
    if (
        os.environ.get("GITHUB_ACTIONS") != "true"
        or worker not in {"gw0", "gw1"}
        or os.environ.get("PYTEST_XDIST_WORKER_COUNT") != "2"
        or os.environ.get("DATABASE_URL") != database_url
        or os.environ.get("DATABASE_URL_UNPOOLED") != database_url
    ):
        raise RuntimeError(
            "Expected two isolated xdist workers on the GitHub Actions test database"
        )
    os.environ["DATABASE_URL"] = os.environ["DATABASE_URL_UNPOOLED"] = f"{database_url}_{worker}"
