"""Universe + timeframes — sourced from policy.yaml (canonical). Tests that
need a different universe monkeypatch policy.yaml or the env vars; production
always reads policy.yaml.
"""
from __future__ import annotations

from trading_sandwich._policy import load_policy


def symbols() -> list[str]:
    return list(load_policy()["universe"])


def timeframes() -> list[str]:
    return list(load_policy()["timeframes"])
