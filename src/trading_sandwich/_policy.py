"""Central `policy.yaml` accessor. Consumers should use these helpers rather than
reading the YAML directly; this gives us one place to change caching or schema
validation when the policy grows.
"""
from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from pathlib import Path

import yaml

_POLICY_PATH = Path("policy.yaml")


@lru_cache(maxsize=1)
def load_policy() -> dict:
    with open(_POLICY_PATH) as f:
        return yaml.safe_load(f)


def get_confidence_threshold(archetype: str) -> Decimal:
    return Decimal(str(load_policy()["per_archetype_confidence_threshold"][archetype]))


def get_cooldown_minutes(archetype: str) -> int:
    return int(load_policy()["per_archetype_cooldown_minutes"][archetype])


def get_dedup_window_minutes() -> int:
    return int(load_policy()["gating"]["dedup_window_minutes"])


def get_regime_thresholds() -> dict:
    return dict(load_policy()["regime"])


def get_funding_threshold(symbol: str) -> tuple[Decimal, Decimal]:
    table = load_policy()["per_symbol_funding_threshold"]
    entry = table.get(symbol, table["default"])
    return Decimal(str(entry["long"])), Decimal(str(entry["short"]))


def reset_cache() -> None:
    """Test hook — policy.yaml changes mid-process (e.g. in a test) need cache bust."""
    load_policy.cache_clear()


def is_trading_enabled() -> bool:
    return bool(load_policy().get("trading_enabled", False))


def get_execution_mode() -> str:
    mode = load_policy().get("execution_mode", "paper")
    if mode not in ("paper", "live"):
        raise ValueError(f"invalid execution_mode: {mode}")
    return mode


def get_proposal_ttl_minutes() -> int:
    return int(load_policy().get("proposal_ttl_minutes", 15))


def get_first_trade_size_multiplier() -> Decimal:
    return Decimal(str(load_policy().get("first_trade_size_multiplier", 0.5)))


def get_claude_daily_triage_cap() -> int:
    return int(load_policy().get("claude_daily_triage_cap", 20))


def get_min_minutes_between_triages() -> int:
    """Global rate limit between any two claude_triaged signals.
    Protects Claude Max session quota when multiple archetypes fire close
    together. 0 disables the gate (fall back to per-archetype cooldowns)."""
    return int(load_policy().get("min_minutes_between_triages", 0))


def get_paper_starting_equity_usd() -> Decimal:
    return Decimal(str(load_policy().get("paper_starting_equity_usd", 10000)))


def get_auto_flatten_on_kill() -> bool:
    return bool(load_policy().get("auto_flatten_on_kill", False))


def get_reconciliation_block_tolerance() -> dict:
    return dict(load_policy().get("reconciliation_block_tolerance", {
        "position_base_drift_pct": 0.5,
        "open_order_count_drift": 0,
    }))


def get_max_order_usd() -> Decimal:
    return Decimal(str(load_policy()["max_order_usd"]))


def get_default_rr_minimum() -> Decimal:
    return Decimal(str(load_policy()["default_rr_minimum"]))


def get_min_stop_distance_atr() -> Decimal:
    return Decimal(str(load_policy()["min_stop_distance_atr"]))


def get_max_stop_distance_atr() -> Decimal:
    return Decimal(str(load_policy()["max_stop_distance_atr"]))


def get_universe_symbols() -> list[str]:
    return list(load_policy()["universe"])


# ----------------------------------------------------------------------
# Dynamic position sizing (Phase 2.7+).
# ----------------------------------------------------------------------

def get_position_sizing_config() -> dict:
    """Return position_sizing block from policy.yaml.

    Falls back to conservative defaults if the section is missing
    (so legacy policy.yaml files don't crash).
    """
    raw = load_policy().get("position_sizing")
    if raw is None:
        return {
            "base_pct": 0.20,
            "min_position_pct": 0.10,
            "max_position_pct": 0.30,
            "min_position_usd": 11,
            "min_equity_to_size_usd": 30,
            "win_rate_anchor": 0.45,
            "win_rate_cap_mult": 1.4,
            "rr_anchor": 1.5,
            "rr_cap_mult": 1.4,
            "sample_anchor": 15,
            "sample_cap_mult": 1.2,
            "sample_mult_floor": 0.5,
            "default_regime_multiplier": 1.0,
        }
    return dict(raw)


class PositionSizingError(Exception):
    """Raised when sizing math refuses a trade (sub-floor or unsafe equity)."""
    def __init__(self, reason: str, raw_pct: float | None = None):
        self.reason = reason
        self.raw_pct = raw_pct
        super().__init__(reason)


def compute_position_size(
    *,
    equity_usd: Decimal,
    win_rate: float,
    expected_rr: float,
    sample_size: int,
    regime_multiplier: float = 1.0,
    is_first_trade: bool = False,
) -> tuple[Decimal, dict]:
    """Compute a USD position size from the proposal's evidence.

    Returns (position_usd, debug_dict). Raises PositionSizingError if the
    setup is below-floor (size would be too small to be worth fees) or if
    equity is below the safety floor (catches auth bugs returning $0).

    debug_dict contains the multiplier breakdown for logging / Discord.
    """
    cfg = get_position_sizing_config()
    eq = float(equity_usd)

    if eq < cfg["min_equity_to_size_usd"]:
        raise PositionSizingError(
            f"equity ${eq:.2f} below min_equity_to_size_usd "
            f"${cfg['min_equity_to_size_usd']} — refuse to size",
        )

    wr_mult = min(win_rate / cfg["win_rate_anchor"], cfg["win_rate_cap_mult"])
    rr_mult = min(expected_rr / cfg["rr_anchor"], cfg["rr_cap_mult"])
    # Sample multiplier has a FLOOR (default 0.5) so sparse samples still
    # produce nonzero sizing. Otherwise sample=0 -> mult=0 -> always refused,
    # which makes new-account trading impossible. Chart-clean setups deserve
    # at least floor-sized exploratory trades to build the evidence base.
    s_mult_floor = cfg.get("sample_mult_floor", 0.5)
    s_mult = max(
        min(sample_size / cfg["sample_anchor"], cfg["sample_cap_mult"]),
        s_mult_floor,
    )

    raw_pct = (
        cfg["base_pct"]
        * wr_mult
        * rr_mult
        * s_mult
        * regime_multiplier
    )

    if raw_pct < cfg["min_position_pct"]:
        raise PositionSizingError(
            f"computed size_pct {raw_pct:.3f} below min "
            f"{cfg['min_position_pct']} — setup not worth the fees",
            raw_pct=raw_pct,
        )

    final_pct = min(raw_pct, cfg["max_position_pct"])

    if is_first_trade:
        final_pct *= float(get_first_trade_size_multiplier())

    position_usd = Decimal(str(round(eq * final_pct, 2)))

    if float(position_usd) < cfg["min_position_usd"]:
        raise PositionSizingError(
            f"computed position ${float(position_usd):.2f} below "
            f"min_position_usd ${cfg['min_position_usd']} — refuse",
            raw_pct=raw_pct,
        )

    return position_usd, {
        "equity_usd": eq,
        "base_pct": cfg["base_pct"],
        "win_rate_mult": wr_mult,
        "rr_mult": rr_mult,
        "sample_mult": s_mult,
        "regime_mult": regime_multiplier,
        "raw_pct": raw_pct,
        "final_pct": final_pct,
        "position_usd": float(position_usd),
        "is_first_trade": is_first_trade,
    }
