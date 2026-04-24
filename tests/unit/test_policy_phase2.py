from decimal import Decimal

from trading_sandwich import _policy


def setup_function():
    _policy.reset_cache()


def test_trading_enabled_default_false():
    assert _policy.is_trading_enabled() is False


def test_execution_mode_default_paper():
    assert _policy.get_execution_mode() == "paper"


def test_proposal_ttl_minutes():
    assert _policy.get_proposal_ttl_minutes() == 15


def test_first_trade_size_multiplier():
    assert _policy.get_first_trade_size_multiplier() == Decimal("0.5")


def test_daily_triage_cap():
    assert _policy.get_claude_daily_triage_cap() == 20


def test_paper_starting_equity_usd():
    assert _policy.get_paper_starting_equity_usd() == Decimal("10000")


def test_auto_flatten_on_kill_default_false():
    assert _policy.get_auto_flatten_on_kill() is False


def test_reconciliation_block_tolerance_keys():
    tol = _policy.get_reconciliation_block_tolerance()
    assert "position_base_drift_pct" in tol
    assert "open_order_count_drift" in tol
