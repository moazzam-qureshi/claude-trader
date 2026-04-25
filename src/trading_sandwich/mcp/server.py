"""FastMCP server for the trading sandwich. Stateless, HTTP/SSE transport.

Tools are registered in tools/*.py via module-level decorators; each tool
module is imported at server boot time so decorators fire.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

# json_response=True returns application/json instead of SSE event-stream
# for streamable-http transport (cleaner for HTTP clients).
# Tools are registered via @mcp.tool() decorators in tools/*.py imported below.
mcp = FastMCP("trading", json_response=True)

# Tool modules are imported here so their @mcp.tool() decorators run at
# server boot. Each module calls mcp.tool(...) on its async functions.
# The package entrypoint lives in __main__.py to avoid the
# `python -m trading_sandwich.mcp.server` double-import trap.
from trading_sandwich.mcp.tools import (  # noqa: F401, E402
    alerts,
    decisions,
    proposals,
    reads,
)
