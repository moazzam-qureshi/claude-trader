"""Unit tests for the Tier 2 (operator-safety) file seed reader."""
from __future__ import annotations

import pytest


def test_safety_seed_reads_known_keys(monkeypatch, tmp_path):
    from trading_sandwich.settings import _safety_seed

    policy_file = tmp_path / "policy.yaml"
    policy_file.write_text(
        "max_account_drawdown_pct: 25\n"
        "max_daily_realized_loss_usd: 35\n"
        "trading_enabled: false\n"
        "auto_flatten_on_kill: false\n"
    )
    monkeypatch.setattr(_safety_seed, "_SAFETY_SEED_PATH", policy_file)
    _safety_seed._cache_clear()

    assert _safety_seed.read("max_account_drawdown_pct") == 25
    assert _safety_seed.read("max_daily_realized_loss_usd") == 35
    assert _safety_seed.read("trading_enabled") is False
    assert _safety_seed.read("auto_flatten_on_kill") is False


def test_safety_seed_rejects_non_tier2_key(monkeypatch, tmp_path):
    from trading_sandwich.settings import _safety_seed

    policy_file = tmp_path / "policy.yaml"
    policy_file.write_text("max_leverage: 1\n")
    monkeypatch.setattr(_safety_seed, "_SAFETY_SEED_PATH", policy_file)
    _safety_seed._cache_clear()

    with pytest.raises(_safety_seed.NotSafetyKeyError):
        _safety_seed.read("max_leverage")  # Tier 1
    with pytest.raises(_safety_seed.NotSafetyKeyError):
        _safety_seed.read("regime_classifier.adx_trend_threshold")  # Tier 3


def test_safety_seed_missing_key_raises(monkeypatch, tmp_path):
    """A Tier 2 key NOT in policy.yaml is a config error — the seed file
    must define every Tier 2 key so the system has a valid fallback even
    if the DB row gets deleted."""
    from trading_sandwich.settings import _safety_seed

    policy_file = tmp_path / "policy.yaml"
    policy_file.write_text("trading_enabled: false\n")
    monkeypatch.setattr(_safety_seed, "_SAFETY_SEED_PATH", policy_file)
    _safety_seed._cache_clear()

    with pytest.raises(KeyError, match="max_account_drawdown_pct"):
        _safety_seed.read("max_account_drawdown_pct")


def test_safety_seed_read_all(monkeypatch, tmp_path):
    from trading_sandwich.settings import _safety_seed, keys

    policy_file = tmp_path / "policy.yaml"
    policy_file.write_text(
        "max_account_drawdown_pct: 25\n"
        "max_daily_realized_loss_usd: 35\n"
        "trading_enabled: false\n"
        "auto_flatten_on_kill: false\n"
    )
    monkeypatch.setattr(_safety_seed, "_SAFETY_SEED_PATH", policy_file)
    _safety_seed._cache_clear()

    out = _safety_seed.read_all()
    assert set(out.keys()) == set(keys.TIER2_SAFETY_KEYS)


def test_safety_seed_validates_real_policy_yaml_has_all_tier2_keys():
    """Pin: the real policy.yaml MUST define every Tier 2 key. If someone
    removes one, this test fails immediately rather than allowing the system
    to boot with no fallback for a circuit breaker."""
    from trading_sandwich.settings import _safety_seed, keys

    _safety_seed._cache_clear()
    out = _safety_seed.read_all()
    for k in keys.TIER2_SAFETY_KEYS:
        assert k in out, f"Tier 2 key {k!r} missing from real policy.yaml"
