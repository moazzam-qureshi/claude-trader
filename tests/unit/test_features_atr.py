import json
from pathlib import Path

import pandas as pd

from trading_sandwich.features.compute import compute_atr


def test_atr_positive():
    data = json.loads(Path("tests/fixtures/candles_btc_1m_synthetic.json").read_text())
    df = pd.DataFrame(data["candles"], columns=["ts", "open", "high", "low", "close", "volume"])
    atr = compute_atr(df["high"], df["low"], df["close"], period=14)
    valid = atr.dropna()
    assert (valid > 0).all()
    assert len(valid) > 0
