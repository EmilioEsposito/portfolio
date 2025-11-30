import pytest
import logfire

@pytest.fixture(scope="session", autouse=True)
def configure_logfire():
    """Configure logfire for testing - logs locally without sending to cloud"""
    logfire.configure(
        send_to_logfire=False,  # Don't send to cloud
        console=logfire.ConsoleOptions(colors='auto'),  # Output to console
    )

