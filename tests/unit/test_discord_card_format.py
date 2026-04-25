from datetime import datetime, timezone

from trading_sandwich.notifications.discord import (
    render_hard_limit_blocked_card,
    render_universe_event_card,
)


def test_render_add_card_includes_required_fields():
    card = render_universe_event_card(
        occurred_at=datetime(2026, 4, 26, 14, 32, tzinfo=timezone.utc),
        event_type="add",
        symbol="SUIUSDT",
        from_tier=None,
        to_tier="observation",
        rationale="caught in 24h gainers, passes fit check",
        reversion_criterion="remove if no signals in 21d",
        shift_id=4721,
        diary_ref="runtime/diary/2026-04-26.md",
    )
    text = card["embeds"][0]["description"]
    assert "SUIUSDT" in text
    assert "observation" in text
    assert "remove if no signals" in text
    assert "shift_id: 4721" in text


def test_hard_limit_card_names_the_limit():
    card = render_hard_limit_blocked_card(
        occurred_at=datetime(2026, 4, 26, 14, 32, tzinfo=timezone.utc),
        attempted={
            "event_type": "promote",
            "symbol": "SOLUSDT",
            "from_tier": "watchlist",
            "to_tier": "core",
            "rationale": "the data warrants it now",
        },
        blocked_by="core_promotions_operator_only",
    )
    text = card["embeds"][0]["description"]
    assert "core_promotions_operator_only" in text
    assert "SOLUSDT" in text
    assert "the data warrants it now" in text
