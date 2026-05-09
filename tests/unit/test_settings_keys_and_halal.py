"""Unit tests for the settings key catalog and the Tier 1 (halal) reader.

These tests pin the three-tier key partition. If anyone (including a future
agent) tries to silently move a circuit breaker out of Tier 2 or move a halal
key out of Tier 1, these tests fail loudly.
"""
from __future__ import annotations

import pytest


# --- Key catalog: tier membership is part of the public contract ------------


def test_tier1_halal_keys_pinned():
    """Tier 1 halal keys are religious/inviolable. NEVER move one out without
    re-reading docs/superpowers/specs/2026-05-10-db-backed-config-amendment.md
    §4.1 and feedback_spot_only_strategies.md memory."""
    from trading_sandwich.settings import keys

    assert keys.TIER1_HALAL_KEYS == frozenset({
        "max_leverage",
        "longs_only",
        "universe.tiers.excluded",
        "universe.hard_limits.excluded_symbols_locked",
    })


def test_tier2_safety_keys_pinned():
    """Tier 2 keys can be overridden by the operator via /safety, but Claude
    can NEVER mutate them. These are the circuit breakers — moving one out
    of Tier 2 makes Claude able to raise its own ceilings."""
    from trading_sandwich.settings import keys

    assert keys.TIER2_SAFETY_KEYS == frozenset({
        "max_account_drawdown_pct",
        "max_daily_realized_loss_usd",
        "trading_enabled",
        "auto_flatten_on_kill",
    })


def test_tiers_disjoint():
    from trading_sandwich.settings import keys

    overlap = keys.TIER1_HALAL_KEYS & keys.TIER2_SAFETY_KEYS
    assert overlap == frozenset(), f"Tier 1 and Tier 2 overlap: {overlap}"


def test_tier_classify_function():
    from trading_sandwich.settings import keys

    assert keys.tier_of("max_leverage") == 1
    assert keys.tier_of("longs_only") == 1
    assert keys.tier_of("max_account_drawdown_pct") == 2
    assert keys.tier_of("trading_enabled") == 2
    assert keys.tier_of("regime_classifier.adx_trend_threshold") == 3
    assert keys.tier_of("strategies.grid_standard.default_levels") == 3
    assert keys.tier_of("anything.else.at.all") == 3


# --- Tier 1 halal reader ----------------------------------------------------


def test_halal_reads_from_policy_yaml(monkeypatch, tmp_path):
    """_halal.read(key) reads only from policy.yaml, never DB."""
    from trading_sandwich.settings import _halal

    policy_file = tmp_path / "policy.yaml"
    policy_file.write_text(
        "max_leverage: 1\n"
        "longs_only: true\n"
        "universe:\n"
        "  tiers:\n"
        "    excluded:\n"
        "      symbols_lending: [AAVEUSDT]\n"
        "      symbols_perp_protocols: [GMXUSDT]\n"
        "      symbols_memecoins: [SHIBUSDT]\n"
        "  hard_limits:\n"
        "    excluded_symbols_locked: [SHIBUSDT, AAVEUSDT, GMXUSDT]\n"
    )
    monkeypatch.setattr(_halal, "_HALAL_POLICY_PATH", policy_file)
    _halal._cache_clear()

    assert _halal.read("max_leverage") == 1
    assert _halal.read("longs_only") is True
    excluded = _halal.read("universe.tiers.excluded")
    assert "symbols_lending" in excluded
    assert _halal.read("universe.hard_limits.excluded_symbols_locked") == [
        "SHIBUSDT", "AAVEUSDT", "GMXUSDT",
    ]


def test_halal_rejects_non_tier1_key(monkeypatch, tmp_path):
    """Reading a Tier 2 or Tier 3 key via _halal.read() is a programming error."""
    from trading_sandwich.settings import _halal, keys

    policy_file = tmp_path / "policy.yaml"
    policy_file.write_text("max_leverage: 1\n")
    monkeypatch.setattr(_halal, "_HALAL_POLICY_PATH", policy_file)
    _halal._cache_clear()

    with pytest.raises(_halal.NotHalalKeyError):
        _halal.read("max_account_drawdown_pct")  # Tier 2
    with pytest.raises(_halal.NotHalalKeyError):
        _halal.read("regime_classifier.adx_trend_threshold")  # Tier 3


def test_halal_violation_error_on_write_attempt():
    """Halal keys have NO write codepath. Attempting any write raises."""
    from trading_sandwich.settings import _halal

    with pytest.raises(_halal.HalalViolationError):
        _halal.refuse_write("max_leverage", new_value=2, reason="any")


def test_halal_max_leverage_must_be_one(monkeypatch, tmp_path):
    """If someone edits policy.yaml to set max_leverage > 1, _halal.validate()
    raises at load time. This is the last line of defense."""
    from trading_sandwich.settings import _halal

    policy_file = tmp_path / "policy.yaml"
    policy_file.write_text("max_leverage: 5\nlongs_only: true\n")
    monkeypatch.setattr(_halal, "_HALAL_POLICY_PATH", policy_file)
    _halal._cache_clear()

    with pytest.raises(_halal.HalalViolationError, match="max_leverage"):
        _halal.validate_loaded()


def test_halal_longs_only_must_be_true(monkeypatch, tmp_path):
    from trading_sandwich.settings import _halal

    policy_file = tmp_path / "policy.yaml"
    policy_file.write_text("max_leverage: 1\nlongs_only: false\n")
    monkeypatch.setattr(_halal, "_HALAL_POLICY_PATH", policy_file)
    _halal._cache_clear()

    with pytest.raises(_halal.HalalViolationError, match="longs_only"):
        _halal.validate_loaded()


def test_halal_read_all_returns_all_tier1(monkeypatch, tmp_path):
    """read_all() returns every Tier 1 key. Used by the snapshot generator
    so policy_snapshot includes the halal values too — a decision row should
    be reproducible without re-reading policy.yaml."""
    from trading_sandwich.settings import _halal, keys

    policy_file = tmp_path / "policy.yaml"
    policy_file.write_text(
        "max_leverage: 1\n"
        "longs_only: true\n"
        "universe:\n"
        "  tiers:\n"
        "    excluded:\n"
        "      symbols_lending: [AAVEUSDT]\n"
        "      symbols_perp_protocols: [GMXUSDT]\n"
        "      symbols_memecoins: [SHIBUSDT]\n"
        "  hard_limits:\n"
        "    excluded_symbols_locked: [SHIBUSDT, AAVEUSDT, GMXUSDT]\n"
    )
    monkeypatch.setattr(_halal, "_HALAL_POLICY_PATH", policy_file)
    _halal._cache_clear()

    snapshot = _halal.read_all()
    for tier1_key in keys.TIER1_HALAL_KEYS:
        assert tier1_key in snapshot, f"halal snapshot missing {tier1_key}"
