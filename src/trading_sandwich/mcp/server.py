"""FastMCP server for the trading sandwich. Stateless, HTTP/SSE transport.

Tools are registered in tools/*.py via module-level decorators; each tool
module is imported at server boot time so decorators fire.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("trading")

# Tool modules are imported here so their @mcp.tool() decorators run at
# server boot. Each module calls mcp.tool(...) on its async functions.
from trading_sandwich.mcp.tools import (  # noqa: F401, E402
    alerts,
    decisions,
    proposals,
    reads,
)


if __name__ == "__main__":
    import sys

    transport = sys.argv[1] if len(sys.argv) > 1 else "sse"
    mcp.run(transport=transport)
