"""Run web and API in process groups; stop both groups on exit or server failure."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    children: list[subprocess.Popen] = []
    stopped = False

    def stop(_signal: int, _frame: object) -> None:
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        for script in ("dev", "fastapi-dev"):
            children.append(subprocess.Popen(["pnpm", script], cwd=root, start_new_session=True))
        while not stopped and all(child.poll() is None for child in children):
            time.sleep(0.2)
        return 0 if stopped else 1
    finally:
        for child in children:
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + 5
        for child in children:
            try:
                child.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()


if __name__ == "__main__":
    raise SystemExit(main())
