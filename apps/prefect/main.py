"""
Prefect Hello World - Standalone Prefect App

This is a standalone Prefect app that demonstrates basic Prefect functionality.
Run with: uv run python main.py
"""

from prefect import flow, task
from datetime import datetime


@task(log_prints=True)
def say_hello(name: str) -> str:
    """A simple task that says hello."""
    message = f"Hello, {name}!"
    print(message)
    return message


@task(log_prints=True)
def get_current_time() -> str:
    """A task that returns the current time."""
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"Current time: {current_time}")
    return current_time


@flow(name="hello-world-flow", log_prints=True)
def hello_world_flow(name: str = "World") -> dict:
    """
    A simple Prefect flow that demonstrates:
    - Task execution
    - Return values
    - Logging

    Args:
        name: The name to greet (default: "World")

    Returns:
        dict with greeting and timestamp
    """
    print(f"Starting hello world flow for: {name}")

    # Execute tasks
    greeting = say_hello(name)
    timestamp = get_current_time()

    result = {
        "greeting": greeting,
        "timestamp": timestamp,
        "status": "success"
    }

    print(f"Flow completed successfully: {result}")
    return result


if __name__ == "__main__":
    # Run the flow directly
    result = hello_world_flow(name="Prefect User")
    print(f"\nFinal result: {result}")
