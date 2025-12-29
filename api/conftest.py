import pytest

from api.src.utils.logfire_config import ensure_logfire_configured

@pytest.fixture(scope="session", autouse=True)
def configure_logfire():
    """Configure logfire for testing - logs locally without sending to cloud"""
    ensure_logfire_configured(mode="test")

