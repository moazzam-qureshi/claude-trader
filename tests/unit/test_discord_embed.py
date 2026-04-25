from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4


def test_render_proposal_embed_contains_all_sections():
    from trading_sandwich.discord.embed import render_proposal_embed
    embed = render_proposal_embed(
        proposal_id=uuid4(),
        symbol="BTCUSDT", side="long", archetype="trend_pullback", timeframe="1h",
        size_usd=Decimal("500"), entry=Decimal("68420"),
        stop=Decimal("67150"), stop_atr_mult=Decimal("1.5"),
        tp=Decimal("71200"), expected_rr=Decimal("2.2"),
        worst_case_loss_usd=Decimal("23.50"), worst_case_pct_equity=Decimal("4.7"),
        similar_count=14, similar_win_rate=Decimal("0.64"), similar_median_r="+0.9R",
        opportunity="x" * 80, risk="y" * 80, profit_case="z" * 80,
        alignment="a" * 40, similar_trades_evidence="b" * 80,
        expires_at=datetime(2026, 4, 25, 12, 15, tzinfo=timezone.utc),
    )
    assert embed["title"].startswith("📈 PROPOSAL")
    for field in embed["fields"]:
        assert field["value"]
    names = {f["name"] for f in embed["fields"]}
    # name fields are decorated with extra info — check by prefix
    name_text = " ".join(names)
    for required in ("OPPORTUNITY", "RISK", "PROFIT CASE", "ALIGNMENT", "EVIDENCE"):
        assert required in name_text
    assert embed["components"]
