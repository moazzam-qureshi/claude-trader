from unittest.mock import MagicMock
from uuid import uuid4

import pytest


def test_parse_custom_id_approve():
    from trading_sandwich.discord.listener import parse_custom_id
    pid = uuid4()
    action, parsed = parse_custom_id(f"approve:{pid}")
    assert action == "approve"
    assert parsed == pid


def test_parse_custom_id_reject():
    from trading_sandwich.discord.listener import parse_custom_id
    pid = uuid4()
    action, parsed = parse_custom_id(f"reject:{pid}")
    assert action == "reject"
    assert parsed == pid


def test_parse_custom_id_invalid():
    from trading_sandwich.discord.listener import parse_custom_id
    with pytest.raises(ValueError):
        parse_custom_id("nonsense")


def test_validate_operator_rejects_other_user(monkeypatch):
    from trading_sandwich.discord.listener import validate_operator
    monkeypatch.setenv("DISCORD_OPERATOR_ID", "111")
    inter = MagicMock()
    inter.user.id = 999
    assert validate_operator(inter) is False


def test_validate_operator_accepts_match(monkeypatch):
    from trading_sandwich.discord.listener import validate_operator
    monkeypatch.setenv("DISCORD_OPERATOR_ID", "111")
    inter = MagicMock()
    inter.user.id = 111
    assert validate_operator(inter) is True
