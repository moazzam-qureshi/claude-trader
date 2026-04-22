import json
from pathlib import Path

import pandas as pd

from trading_sandwich.features.compute import compute_ema


def _load() -> pd.DataFrame:
    data = json.loads(Path("tests/fixtures/candles_btc_1m_synthetic.json").read_text())
    df = pd.DataFrame(data["candles"], columns=["ts", "open", "high", "low", "close", "volume"])
    df["close_time"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df


def test_ema_21_matches_manual_calc():
    df = _load()
    ema = compute_ema(df["close"], period=21)
    assert ema.iloc[:20].isna().all()
    expected_initial = df["close"].iloc[:21].mean()
    assert abs(ema.iloc[20] - expected_initial) < 0.01


def test_ema_21_returns_same_length_series():
    df = _load()
    ema = compute_ema(df["close"], period=21)
    assert len(ema) == len(df)
