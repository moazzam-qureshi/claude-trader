def test_phase2_models_import():
    from trading_sandwich.db.models_phase2 import (
        Alert,
        KillSwitchState,
        Order,
        OrderModification,
        Position,
        RiskEvent,
        TradeProposal,
    )
    assert TradeProposal.__tablename__ == "trade_proposals"
    assert Order.__tablename__ == "orders"
    assert OrderModification.__tablename__ == "order_modifications"
    assert Position.__tablename__ == "positions"
    assert RiskEvent.__tablename__ == "risk_events"
    assert KillSwitchState.__tablename__ == "kill_switch_state"
    assert Alert.__tablename__ == "alerts"


def test_trade_proposal_has_prose_columns():
    from trading_sandwich.db.models_phase2 import TradeProposal
    cols = {c.name for c in TradeProposal.__table__.columns}
    for prose in ["opportunity", "risk", "profit_case", "alignment", "similar_trades_evidence"]:
        assert prose in cols


def test_order_has_policy_version_column():
    from trading_sandwich.db.models_phase2 import Order
    cols = {c.name for c in Order.__table__.columns}
    assert "policy_version" in cols
    assert "client_order_id" in cols
    assert "execution_mode" in cols
