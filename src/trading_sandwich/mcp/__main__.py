"""Module entrypoint. Run with: python -m trading_sandwich.mcp <transport>

Why this file exists: when invoked as `python -m trading_sandwich.mcp.server`,
that module is loaded as __main__, and any subsequent
`from trading_sandwich.mcp.server import mcp` re-imports it as a *different*
module — meaning @mcp.tool() decorators register on a different FastMCP
instance than the one mcp.run() actually serves. Tools/list returns empty.

Routing through __main__.py instead means trading_sandwich.mcp.server is
always loaded by its canonical import name, so the decorator-registered
mcp object IS the one that mcp.run() serves.
"""
from __future__ import annotations

import os
import sys

from trading_sandwich.mcp.server import mcp

if __name__ == "__main__":
    transport = sys.argv[1] if len(sys.argv) > 1 else "streamable-http"

    if transport in ("sse", "streamable-http"):
        from mcp.server.transport_security import TransportSecuritySettings

        mcp.settings.host = os.environ.get("MCP_HOST", "0.0.0.0")
        mcp.settings.port = int(os.environ.get("MCP_PORT", "8765"))
        mcp.settings.transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=False,
            allowed_hosts=["*"],
            allowed_origins=["*"],
        )

    mcp.run(transport=transport)
