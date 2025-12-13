from dbos import DBOS, DBOSConfig
import os
import logfire

# --- DBOS Configuration ---

dbos_config: DBOSConfig = {
    "name": "dbos_portfolio",
    "database_url": os.getenv("DATABASE_URL"),
    # "log_level": "WARN",
}
DBOS(config=dbos_config)


def launch_dbos():
    """Launch DBOS runtime."""
    DBOS.launch()
    logfire.info("DBOS launched for email approval demo")
