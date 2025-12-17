from __future__ import annotations

import os
import logging
import warnings
from typing import Optional

import logfire
from dotenv import find_dotenv, load_dotenv
from logfire.sampling import TailSamplingSpanInfo

_CONFIGURED: bool = False
_CONFIGURED_MODE: Optional[str] = None  # e.g. "prod", "test"


def _load_local_env_if_possible() -> None:
    # Load local development variables (does not impact preview/production)
    try:
        load_dotenv(find_dotenv(".env"), override=True)
    except PermissionError:
        # In some sandboxed environments the `.env` file is intentionally not readable.
        # Production/preview rely on real environment variables, so it's safe to skip.
        pass


def _drop_dbos_sys_traces(span_info: TailSamplingSpanInfo) -> float:
    """
    Tail-sampling callback to drop DBOS internal DB chatter (`*_dbos_sys`) while preserving other traces.
    """
    attrs = span_info.span.attributes or {}
    db_name = attrs.get("db.name") or attrs.get("db.instance")
    span_name = (getattr(span_info.span, "name", "") or "").lower()
    statement = attrs.get("db.statement")
    statement_l = str(statement).lower() if statement is not None else ""

    is_dbos_sys = (
        (db_name is not None and "dbos_sys" in str(db_name))
        or ("dbos_sys" in span_name)
        or ("dbos_sys" in statement_l)
    )
    if is_dbos_sys:
        return 0.0

    # Buffer until end so we can decide with full attributes, then include.
    return 1.0 if span_info.event == "end" else 0.0


def ensure_logfire_configured(
    *,
    mode: str = "prod",
    service_name: str = "fastapi",
    environment: Optional[str] = None,
) -> None:
    """
    Configure Logfire exactly once per process.

    - **prod mode**: send traces/logs to Logfire (default behavior in app runtime)
    - **test mode**: do not send to Logfire; show console output

    This exists because different entrypoints (FastAPI app, migrations, standalone scripts/tests)
    may import different modules first, but we only want one Logfire initialization per boot.
    """
    global _CONFIGURED, _CONFIGURED_MODE
    if _CONFIGURED:
        return

    # OpenTelemetry can emit a noisy warning when some instrumentations try to set attributes
    # on spans after they've been ended (common with buffering/dropping processors).
    warnings.filterwarnings("ignore", message=r".*Setting attribute on ended span\..*")
    # In some environments this message is emitted via stdlib logging with a minimal formatter
    # so it shows up as a bare line. Filter it at the root logger to keep other warnings intact.
    class _DropEndedSpanNoise(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
            msg = record.getMessage()
            return msg.strip() != "Setting attribute on ended span."

    _noise_filter = _DropEndedSpanNoise()
    logging.getLogger().addFilter(_noise_filter)
    logging.getLogger("opentelemetry.sdk.trace").addFilter(_noise_filter)

    _load_local_env_if_possible()

    env_name = environment or os.getenv("RAILWAY_ENVIRONMENT_NAME", "local")

    if mode == "test":
        logfire.configure(
            send_to_logfire=False,
            console=logfire.ConsoleOptions(colors="auto"),
            sampling=logfire.SamplingOptions(head=1.0, tail=_drop_dbos_sys_traces),
        )
    else:
        logfire.configure(
            service_name=service_name,
            environment=env_name,
            send_to_logfire=True,
            sampling=logfire.SamplingOptions(head=1.0, tail=_drop_dbos_sys_traces),
        )

    _CONFIGURED = True
    _CONFIGURED_MODE = mode


def is_logfire_configured() -> bool:
    return _CONFIGURED

