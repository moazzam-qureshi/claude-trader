"""Strategy ↔ regime compatibility — Phase 3 plan Task 1.9.

Declarative map (lives in policy.yaml under
`strategy_regime_compatibility`) of which strategy types are eligible
to run in which regimes. Read by the Portfolio Strategist (deciding
where to deploy) and the strategy-worker (deciding when an existing
strategy should pause itself because its regime no longer matches).

Two safety nets:

  1. STRATEGY_CATALOG — the fixed set of strategy IDs spec §6.2 names.
     The loader rejects compat blocks referencing strategies outside
     the catalog; this catches typos and prevents silent
     unimplemented-strategy references.

  2. is_compatible() fail-closed — strategy not in compat → False.
     A stale strategy reference in code shouldn't accidentally pass
     compat checks; better to refuse the deployment.

Wildcard "*" means "all regimes." Used for strategies that should
run in any regime (e.g. dca_calendar, rebalance_threshold).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml

from trading_sandwich.strategies.base import Regime


# Spec §6.2 strategy IDs — the canonical catalog. New strategies must
# be added here AND implemented; this set is the contract between
# config and code.
STRATEGY_CATALOG: frozenset[str] = frozenset({
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
})


_RegimeOrWildcard = Literal["*"] | list[Regime]


@dataclass(frozen=True)
class StrategyRegimeCompat:
    """Loaded compat map. compatibility[strategy_type] is either:
      - the literal string "*" (wildcard — compatible with every regime), or
      - a list[Regime] (specific regimes only).

    Empty dict is legal — fail-closed default for fresh bootstrap.
    """

    compatibility: dict[str, _RegimeOrWildcard] = field(default_factory=dict)


def _coerce_regime(value: str) -> Regime:
    """Accept either uppercase enum-name ('TREND_UP') or lowercase
    enum-value ('trend_up'). Spec §6.2 yaml uses uppercase; we
    normalize on load."""
    try:
        return Regime[value]
    except KeyError:
        try:
            return Regime(value.lower())
        except ValueError as e:
            raise ValueError(f"unknown regime {value!r}") from e


def load_compat_from_yaml(policy_path: Path) -> StrategyRegimeCompat:
    """Read policy.yaml's `strategy_regime_compatibility` block. Returns
    an empty StrategyRegimeCompat if the block is absent. Raises
    ValueError on unknown strategy IDs or unknown regime values."""
    raw = yaml.safe_load(policy_path.read_text()) or {}
    block = raw.get("strategy_regime_compatibility")
    if not block:
        return StrategyRegimeCompat(compatibility={})

    out: dict[str, _RegimeOrWildcard] = {}
    for key, value in block.items():
        if key not in STRATEGY_CATALOG:
            raise ValueError(
                f"strategy_regime_compatibility references unknown strategy "
                f"{key!r}; must be one of STRATEGY_CATALOG ({len(STRATEGY_CATALOG)} "
                f"entries). Add to catalog if intentional."
            )
        if value == ["*"] or value == "*":
            out[key] = "*"
            continue
        if not isinstance(value, list):
            raise ValueError(
                f"strategy_regime_compatibility[{key!r}] must be a list "
                f"of regimes or [\"*\"], got {type(value).__name__}"
            )
        regimes: list[Regime] = []
        for item in value:
            regimes.append(_coerce_regime(item))
        out[key] = regimes
    return StrategyRegimeCompat(compatibility=out)


def is_compatible(
    strategy_type: str,
    regime: Regime,
    compat: StrategyRegimeCompat,
) -> bool:
    """Returns True iff the compat map declares this strategy compatible
    with this regime. Fail-closed: unknown strategy → False."""
    entry = compat.compatibility.get(strategy_type)
    if entry is None:
        return False
    if entry == "*":
        return True
    return regime in entry
