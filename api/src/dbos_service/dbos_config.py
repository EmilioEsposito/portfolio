from dbos import DBOS, DBOSConfig
import os
import logfire

# --- DBOS Configuration ---

# Clear any existing DBOS state before re-initializing (handles pytest re-imports)
try:
    DBOS.destroy(destroy_registry=True)
except Exception:
    pass

_dbos_launched: bool = False

# idemptotent launcher
def launch_dbos():
    """Launch DBOS runtime."""
    dbos_config: DBOSConfig = {
        "name": "dbos_portfolio",
        "database_url": os.getenv("DATABASE_URL"),
        # "log_level": "WARN",
        "enable_otlp": True,
        # "run_admin_server": False,  # Disable to avoid port conflicts in tests/dev
    }
    DBOS(config=dbos_config)

    global _dbos_launched
    if _dbos_launched:
        return
    DBOS.launch()
    _dbos_launched = True
    logfire.info("DBOS launched.")


def shutdown_dbos(*, workflow_completion_timeout_sec: int = 0) -> None:
    """Shutdown DBOS runtime (useful for tests/CLI runs)."""
    global _dbos_launched
    try:
        DBOS.destroy(workflow_completion_timeout_sec=workflow_completion_timeout_sec)
    finally:
        _dbos_launched = False
