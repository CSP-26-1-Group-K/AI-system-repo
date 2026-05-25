"""Compatibility import for the live smart-home control server.

The implementation now lives in smart_home.live.server. This module remains so
older scripts or uvicorn targets keep working during development.
"""

from smart_home.live.server import app, bridge

__all__ = ["app", "bridge"]
