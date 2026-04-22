import json
from pathlib import Path

import pandas as pd

from trading_sandwich.features.compute import compute_rsi


def test_rsi_bounds():
    data = json.loads(Path("tests/fixtures/candles_btc_1m_synthetic.json").read_text())
    df = pd.DataFrame(data["candles"], columns=["ts", "open", "high", "low", "close", "volume"])
    rsi = compute_rsi(df["close"], period=14)
    valid = rsi.dropna()
    assert (valid >= 0).all() and (valid <= 100).all()
    assert len(valid) > 0
