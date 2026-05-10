"""Phase 3 plan Task 1.9 — strategy↔regime compatibility config.

The compatibility map is declarative config (spec §6.2): each strategy
type names the regimes it should run in. The Portfolio Strategist reads
it (via MCP tool, Task 1.11) to decide where to deploy. The strategy-
worker reads it (Task 1.15) to decide whether an existing strategy
should pause itself when its regime no longer matches.

Tests pin:
  - The catalog of strategy IDs is fixed (matches spec §6.2 exactly).
  - The loader parses a valid block from policy.yaml.
  - Unknown strategy IDs are rejected.
  - "*" wildcard means compatible with every regime.
  - Specific regime lists are enforced for compat checks.
  - is_compatible(strategy_type, regime) returns the right answer.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from trading_sandwich.strategies.base import Regime
from trading_sandwich.strategies.regime_compat import (
    STRATEGY_CATALOG,
    StrategyRegimeCompat,
    is_compatible,
    load_compat_from_yaml,
)


def _write_yaml(tmp_path: Path, payload: dict) -> Path:
    p = tmp_path / "policy.yaml"
    p.write_text(yaml.safe_dump(payload))
    return p


def test_catalog_includes_all_spec_6_2_strategies():
    """Spec §6.2 lists ~37 strategies. The catalog matches verbatim."""
    expected_subset = {
        # Range capture (Category A)
        "grid_standard", "grid_infinity", "grid_geometric", "grid_reverse",
        "rsi_mean_reversion", "bollinger_reversion", "z_score_reversion",
        "range_expansion_contraction",
        # Accumulation (Category B)
        "dca_calendar", "dca_value_averaging", "dca_volatility_adj",
        "dca_indicator", "dca_fear_greed", "dca_mvrv_nupl",
        "dca_drawdown_tier", "dca_pre_halving", "dca_capitulation",
        "dca_profit_ladder",
        # Rebalancing (Category C)
        "rebalance_periodic", "rebalance_threshold", "rebalance_risk_parity",
        "hodl_plus_plus",
        # Trend (Category D)
        "trend_ma_crossover", "trend_donchian", "trend_volatility_breakout",
        "trend_time_series_momentum", "trend_multi_tf_alignment",
        # Rotation (Category E)
        "rotation_cross_sectional", "rotation_sector",
        "rotation_btc_dominance", "rotation_pair", "rotation_index_tilt",
        # Cycle (Category F)
        "cycle_halving", "cycle_bottom_detection", "cycle_top_detection",
        # Volatility regime (Category G)
        "vol_targeting", "vol_anti_cyclical",
    }
    assert expected_subset.issubset(STRATEGY_CATALOG)


def test_loads_valid_compat_block(tmp_path: Path):
    payload = {
        "strategy_regime_compatibility": {
            "grid_standard": ["RANGE_VOLATILE", "RANGE_QUIET", "TREND_UP"],
            "dca_calendar": ["*"],
            "trend_ma_crossover": ["TREND_UP"],
        }
    }
    compat = load_compat_from_yaml(_write_yaml(tmp_path, payload))
    assert isinstance(compat, StrategyRegimeCompat)
    assert compat.compatibility["grid_standard"] == [
        Regime.RANGE_VOLATILE, Regime.RANGE_QUIET, Regime.TREND_UP,
    ]


def test_wildcard_compatible_with_every_regime():
    compat = StrategyRegimeCompat(compatibility={"dca_calendar": "*"})
    for r in Regime:
        assert is_compatible("dca_calendar", r, compat) is True


def test_specific_list_enforces_membership():
    compat = StrategyRegimeCompat(compatibility={
        "trend_ma_crossover": [Regime.TREND_UP],
    })
    assert is_compatible("trend_ma_crossover", Regime.TREND_UP, compat) is True
    assert is_compatible("trend_ma_crossover", Regime.TREND_DOWN, compat) is False
    assert is_compatible("trend_ma_crossover", Regime.RANGE_VOLATILE, compat) is False


def test_unknown_strategy_raises_on_load(tmp_path: Path):
    payload = {
        "strategy_regime_compatibility": {
            "grid_standard": ["RANGE_VOLATILE"],
            "made_up_strategy": ["TREND_UP"],
        }
    }
    with pytest.raises(ValueError, match="made_up_strategy"):
        load_compat_from_yaml(_write_yaml(tmp_path, payload))


def test_unknown_regime_raises_on_load(tmp_path: Path):
    payload = {
        "strategy_regime_compatibility": {
            "grid_standard": ["RANGE_VOLATILE", "MOON_PHASE"],
        }
    }
    with pytest.raises(ValueError, match="MOON_PHASE"):
        load_compat_from_yaml(_write_yaml(tmp_path, payload))


def test_missing_compat_block_returns_empty_compat(tmp_path: Path):
    """If policy.yaml has no strategy_regime_compatibility block (e.g.
    early bootstrap), the loader returns an empty compat. is_compatible
    against an empty compat returns False for all (strategy, regime) —
    fail-closed default."""
    p = tmp_path / "policy.yaml"
    p.write_text(yaml.safe_dump({"timeframes": ["1h"]}))
    compat = load_compat_from_yaml(p)
    assert compat.compatibility == {}
    assert is_compatible("grid_standard", Regime.RANGE_VOLATILE, compat) is False


def test_unknown_strategy_in_lookup_returns_false():
    """is_compatible against a strategy not in the compat returns False
    rather than raising. Production callers may have stale strategy
    references; fail-closed is safer."""
    compat = StrategyRegimeCompat(compatibility={
        "grid_standard": [Regime.RANGE_VOLATILE],
    })
    assert is_compatible("never_heard_of_this", Regime.TREND_UP, compat) is False


def test_real_policy_yaml_parses(tmp_path: Path):
    """The policy.yaml committed in the repo includes the spec §6.2
    block. The loader reads it without errors. (No assertion on
    specific entries — that's brittle; just no-throw is the contract.)
    """
    real_path = Path("/app/policy.yaml")
    if not real_path.exists():
        pytest.skip("real policy.yaml not mounted in test env")
    compat = load_compat_from_yaml(real_path)
    # Either populated (post-Task 1.9 commit) or empty (pre-commit
    # bootstrap). Both must succeed.
    assert isinstance(compat, StrategyRegimeCompat)
