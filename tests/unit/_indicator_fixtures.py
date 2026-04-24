"""Shared deterministic candle DataFrames for indicator tests."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


def load_btc_1m_synthetic() -> pd.DataFrame:
    """30 BTC 1m candles crafted in Phase 0. Good enough for most warmup tests."""
    data = json.loads(Path("tests/fixtures/candles_btc_1m_synthetic.json").read_text())
    df = pd.DataFrame(
        data["candles"],
        columns=["ts", "open", "high", "low", "close", "volume"],
    )
    df["close_time"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df


def linear_uptrend(n: int = 300) -> pd.DataFrame:
    """n 1m candles rising linearly by 0.5 per bar, high/low = close ± 0.3,
    volume = 10. Useful for trend indicator tests that need ≥200 bars (EMA-200).
    """
    rows = []
    for i in range(n):
        c = 100.0 + i * 0.5
        rows.append({
            "ts": 1700000000000 + i * 60_000,
            "open": c - 0.1, "high": c + 0.3, "low": c - 0.3,
            "close": c, "volume": 10.0,
        })
    df = pd.DataFrame(rows)
    df["close_time"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df


def noisy_flat(n: int = 300, seed: int = 42) -> pd.DataFrame:
    """n 1m candles oscillating around 100 with low variance. Useful for
    range/squeeze regime tests.
    """
    import numpy as np
    rng = np.random.default_rng(seed)
    closes = 100.0 + rng.standard_normal(n) * 0.5
    rows = []
    for i, c in enumerate(closes):
        rows.append({
            "ts": 1700000000000 + i * 60_000,
            "open": c - 0.05, "high": c + 0.2, "low": c - 0.2,
            "close": float(c), "volume": 10.0,
        })
    df = pd.DataFrame(rows)
    df["close_time"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df
