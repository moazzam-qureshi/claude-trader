"""Universe + timeframes — sourced from policy.yaml (canonical). Tests that
need a different universe monkeypatch policy.yaml or the env vars; production
always reads policy.yaml.
"""
from __future__ import annotations

from trading_sandwich._policy import load_policy


def symbols() -> list[str]:
    """Flat list of tradeable symbols across core+active+observation tiers.

    Phase 3 (spec §6.1) renamed the second tier from `watchlist` to
    `active`. Excluded symbols are not returned (they are not tradeable).
    """
    u = load_policy()["universe"]
    if isinstance(u, dict) and "tiers" in u:
        out: list[str] = []
        for tier in ("core", "active", "observation"):
            out.extend(u["tiers"].get(tier, {}).get("symbols", []))
        return out
    return list(u)


def timeframes() -> list[str]:
    return list(load_policy()["timeframes"])
