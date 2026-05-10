"""Phase 3 plan Task 1.14 — Discord strategy button + modal logic.

Tests the parser + handler-dispatch for strategy buttons. The view
rendering (discord.py View / Modal classes) is declarative and not
covered here — it's exercised end-to-end at runtime when the bot
posts a card.
"""
from __future__ import annotations

import pytest

from trading_sandwich.discord.strategies_buttons import (
    parse_strategy_button_id,
    UnknownStrategyButtonError,
)


def test_parse_pause_button_id():
    action, sid = parse_strategy_button_id("strat_pause:42")
    assert action == "pause"
    assert sid == 42


def test_parse_resume_button_id():
    action, sid = parse_strategy_button_id("strat_resume:1234")
    assert action == "resume"
    assert sid == 1234


def test_parse_winddown_button_id():
    action, sid = parse_strategy_button_id("strat_winddown:7")
    assert action == "wind_down"
    assert sid == 7


def test_parse_adjust_button_id():
    action, sid = parse_strategy_button_id("strat_adjust:99")
    assert action == "adjust"
    assert sid == 99


def test_parse_rejects_unknown_action():
    with pytest.raises(UnknownStrategyButtonError):
        parse_strategy_button_id("strat_yolo:1")


def test_parse_rejects_non_strategy_prefix():
    """Non-strategy buttons (e.g. proposal approve:<uuid>) raise so the
    listener can fall through to the existing approval handlers."""
    with pytest.raises(UnknownStrategyButtonError):
        parse_strategy_button_id("approve:550e8400-e29b-41d4-a716-446655440000")


def test_parse_rejects_malformed_id():
    with pytest.raises(UnknownStrategyButtonError):
        parse_strategy_button_id("strat_pause")
    with pytest.raises(UnknownStrategyButtonError):
        parse_strategy_button_id("strat_pause:not-a-number")


def test_modal_payload_validation_accepts_valid_json():
    """The /strategies adjust modal asks for a JSON params patch.
    Validate before passing to adjust_params."""
    from trading_sandwich.discord.strategies_buttons import (
        ParamsPatchValidationError,
        validate_params_patch,
    )
    patch = validate_params_patch('{"high": 75000, "levels": 6}')
    assert patch == {"high": 75000, "levels": 6}


def test_modal_payload_validation_rejects_non_object_json():
    from trading_sandwich.discord.strategies_buttons import (
        ParamsPatchValidationError,
        validate_params_patch,
    )
    with pytest.raises(ParamsPatchValidationError):
        validate_params_patch("[1, 2, 3]")
    with pytest.raises(ParamsPatchValidationError):
        validate_params_patch('"just a string"')


def test_modal_payload_validation_rejects_invalid_json():
    from trading_sandwich.discord.strategies_buttons import (
        ParamsPatchValidationError,
        validate_params_patch,
    )
    with pytest.raises(ParamsPatchValidationError):
        validate_params_patch("not json")


def test_strategy_card_view_has_expected_button_custom_ids():
    """build_strategy_card_view produces a discord.py View with the
    four buttons; their custom_id strings are what the listener parses.
    Test the contract by reading the View's children."""
    from trading_sandwich.discord.strategies_buttons import (
        build_strategy_card_view,
    )
    view = build_strategy_card_view(strategy_id=42)
    custom_ids = [c.custom_id for c in view.children]
    assert "strat_pause:42" in custom_ids
    assert "strat_resume:42" in custom_ids
    assert "strat_winddown:42" in custom_ids
    assert "strat_adjust:42" in custom_ids
