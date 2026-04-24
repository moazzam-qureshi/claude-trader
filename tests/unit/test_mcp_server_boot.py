def test_mcp_server_instance_exists():
    from trading_sandwich.mcp.server import mcp
    assert mcp is not None
    assert mcp.name == "trading"


def test_mcp_server_has_registered_tools_attr():
    from trading_sandwich.mcp.server import mcp
    # After all tools are wired (later tasks) this will grow; for now the
    # server must boot cleanly.
    assert hasattr(mcp, "_tool_manager") or hasattr(mcp, "tools")
