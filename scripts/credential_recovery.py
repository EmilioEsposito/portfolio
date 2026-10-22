#!/usr/bin/env python3
"""Compatibility entrypoint; canonical implementation: credentials.recovery."""

import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
_implementation = importlib.import_module("credentials.recovery")
if __name__ == "__main__":
    raise SystemExit(_implementation.main())
# Preserve imports and patching for existing operators during the cutover.
sys.modules[__name__] = _implementation
globals().update({k: v for k, v in vars(_implementation).items() if not k.startswith("__")})
